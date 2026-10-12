import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, String, create_engine, func, inspect, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker
from passlib.context import CryptContext
from starlette.middleware.sessions import SessionMiddleware


logger = logging.getLogger(__name__)
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./pos.db")
SESSION_SECRET_KEY = os.getenv("SESSION_SECRET_KEY", "local-dev-only-change-this-secret")
SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
password_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class Base(DeclarativeBase):
    pass


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    products: Mapped[list["Product"]] = relationship(back_populates="category")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('admin', 'cashier')", name="ck_users_role"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="cashier")
    orders: Mapped[list["Order"]] = relationship(back_populates="cashier")

    def set_password(self, password: str) -> None:
        if len(password.encode("utf-8")) > 72:
            raise ValueError("Bcrypt passwords cannot exceed 72 UTF-8 bytes")
        self.password_hash = password_context.hash(password)

    def verify_password(self, password: str) -> bool:
        if len(password.encode("utf-8")) > 72:
            return False
        return password_context.verify(password, self.password_hash)


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("price >= 0", name="ck_products_price_nonnegative"),
        CheckConstraint("stock_qty >= 0", name="ck_products_stock_nonnegative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    sku: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    barcode: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    stock_qty: Mapped[int] = mapped_column(nullable=False, default=0)
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), nullable=True, index=True
    )

    order_items: Mapped[list["OrderItem"]] = relationship(back_populates="product")
    category: Mapped[Category | None] = relationship(back_populates="products")


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (CheckConstraint("total >= 0", name="ck_orders_total_nonnegative"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=0)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    discount_type: Mapped[str] = mapped_column(String(20), nullable=False, default="flat")
    discount_value: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    total: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    payment_method: Mapped[str] = mapped_column(String(50), nullable=False, default="cash")
    cashier_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )
    cashier: Mapped[User | None] = relationship(back_populates="orders")


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_order_items_quantity_positive"),
        CheckConstraint("unit_price >= 0", name="ck_order_items_price_nonnegative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    product_name: Mapped[str] = mapped_column(String(200), nullable=False)
    product_sku: Mapped[str] = mapped_column(String(100), nullable=False)
    quantity: Mapped[int] = mapped_column(nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)

    order: Mapped[Order] = relationship(back_populates="items")
    product: Mapped[Product] = relationship(back_populates="order_items")


class CheckoutItem(BaseModel):
    product_id: int = Field(gt=0)
    quantity: int = Field(gt=0)


class CheckoutRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    items: list[CheckoutItem] = Field(min_length=1)
    tax_rate: Decimal = Field(default=Decimal("0"), ge=0, le=100, decimal_places=2)
    discount_type: Literal["flat", "percentage"] = "flat"
    discount_value: Decimal = Field(default=Decimal("0"), ge=0, max_digits=10, decimal_places=2)
    payment_method: str = Field(default="cash", min_length=1, max_length=50, pattern=r"\S")


class LoginRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    email: str = Field(min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=1, max_length=128)


class CashierCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    email: str = Field(min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    full_name: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=8, max_length=72)


class CategoryInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=100)


class ProductInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)
    sku: str = Field(min_length=1, max_length=100)
    barcode: str = Field(min_length=1, max_length=100)
    price: Decimal = Field(ge=0, max_digits=10, decimal_places=2)
    stock_qty: int = Field(ge=0)
    category_id: int | None = Field(default=None, gt=0)


def migrate_existing_schema():
    migrations = {
        "products": {
            "category_id": "INTEGER REFERENCES categories(id) ON DELETE SET NULL",
        },
        "orders": {
            "subtotal": "NUMERIC(10, 2) NOT NULL DEFAULT 0",
            "tax_rate": "NUMERIC(5, 2) NOT NULL DEFAULT 0",
            "tax_amount": "NUMERIC(10, 2) NOT NULL DEFAULT 0",
            "discount_type": "VARCHAR(20) NOT NULL DEFAULT 'flat'",
            "discount_value": "NUMERIC(10, 2) NOT NULL DEFAULT 0",
            "discount_amount": "NUMERIC(10, 2) NOT NULL DEFAULT 0",
            "payment_method": "VARCHAR(50) NOT NULL DEFAULT 'cash'",
            "cashier_id": "INTEGER REFERENCES users(id)",
        },
        "order_items": {
            "product_name": "VARCHAR(200) NOT NULL DEFAULT ''",
            "product_sku": "VARCHAR(100) NOT NULL DEFAULT ''",
        },
    }
    inspector = inspect(engine)
    with engine.begin() as connection:
        for table, columns in migrations.items():
            existing = {column["name"] for column in inspector.get_columns(table)}
            for name, definition in columns.items():
                if name not in existing:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))
        connection.execute(text(
            "UPDATE order_items SET product_name = "
            "(SELECT name FROM products WHERE products.id = order_items.product_id) "
            "WHERE product_name = ''"
        ))
        connection.execute(text(
            "UPDATE order_items SET product_sku = "
            "(SELECT sku FROM products WHERE products.id = order_items.product_id) "
            "WHERE product_sku = ''"
        ))
        connection.execute(text("UPDATE orders SET subtotal = total WHERE subtotal = 0 AND total != 0"))


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    migrate_existing_schema()
    from seed import seed_products
    from seed import seed_admin

    with SessionLocal() as db:
        seed_products(db)
        seed_admin(db)
    yield


app = FastAPI(title="storePOS", lifespan=lifespan)
if SESSION_SECRET_KEY == "local-dev-only-change-this-secret":
    logger.warning("Using the development session secret; configure SESSION_SECRET_KEY before deployment.")
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET_KEY,
    session_cookie="pos_session",
    max_age=60 * 60 * 12,
    same_site="lax",
    https_only=SESSION_COOKIE_SECURE,
)


def get_db():
    with SessionLocal() as db:
        yield db


DatabaseSession = Annotated[Session, Depends(get_db)]


def get_optional_current_user(request: Request, db: DatabaseSession) -> User | None:
    user_id = request.session.get("user_id")
    if type(user_id) is not int:
        if user_id is not None:
            request.session.clear()
        return None
    user = db.get(User, user_id)
    if user is None:
        request.session.clear()
    return user


OptionalCurrentUser = Annotated[User | None, Depends(get_optional_current_user)]


def get_current_user(user: OptionalCurrentUser) -> User:
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_admin(user: CurrentUser) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Administrator access required")
    return user


AdminUser = Annotated[User, Depends(require_admin)]


def serialize_user(user: User):
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
    }


@app.get("/test-db")
def test_database(db: DatabaseSession, _: AdminUser):
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        logger.exception("Database connectivity check failed")
        raise HTTPException(status_code=503, detail="Database connection failed") from exc
    return {"status": "success", "message": "Database connection is available"}


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, user: OptionalCurrentUser):
    if user is not None:
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(request=request, name="login.html")


@app.get("/", response_class=HTMLResponse)
def register(request: Request, user: OptionalCurrentUser):
    if user is None:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"user": serialize_user(user)},
    )


@app.get("/orders", response_class=HTMLResponse)
def orders_page(request: Request, user: OptionalCurrentUser):
    if user is None:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    if user.role != "admin":
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request=request,
        name="orders.html",
        context={"user": serialize_user(user)},
    )


@app.post("/api/auth/login")
def login(payload: LoginRequest, request: Request, db: DatabaseSession):
    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    if user is None or not user.verify_password(payload.password):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    request.session.clear()
    request.session["user_id"] = user.id
    return {"user": serialize_user(user)}


@app.get("/api/auth/me")
def current_session_user(user: CurrentUser):
    return serialize_user(user)


@app.post("/api/auth/logout")
def logout(request: Request):
    request.session.clear()
    return {"message": "Logged out"}


@app.get("/api/users")
def list_users(db: DatabaseSession, _: AdminUser):
    users = db.scalars(select(User).order_by(User.full_name)).all()
    return [serialize_user(user) for user in users]


@app.post("/api/users", status_code=status.HTTP_201_CREATED)
def create_cashier(payload: CashierCreate, db: DatabaseSession, _: AdminUser):
    if len(payload.password.encode("utf-8")) > 72:
        raise HTTPException(status_code=422, detail="Password cannot exceed 72 UTF-8 bytes")
    cashier = User(
        email=payload.email.lower(),
        full_name=payload.full_name,
        role="cashier",
    )
    cashier.set_password(payload.password)
    db.add(cashier)
    try:
        db.commit()
        db.refresh(cashier)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="An account with that email already exists") from exc
    return serialize_user(cashier)


def serialize_product(product: Product):
    return {
        "id": product.id,
        "name": product.name,
        "sku": product.sku,
        "barcode": product.barcode,
        "price": float(product.price),
        "stock_qty": product.stock_qty,
        "category_id": product.category_id,
        "category_name": product.category.name if product.category else None,
    }


@app.get("/api/categories")
def list_categories(db: DatabaseSession, _: CurrentUser):
    return [
        {"id": category.id, "name": category.name}
        for category in db.scalars(select(Category).order_by(Category.name)).all()
    ]


@app.get("/api/categories/{category_id}")
def get_category(category_id: int, db: DatabaseSession, _: CurrentUser):
    category = db.get(Category, category_id)
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found")
    return {"id": category.id, "name": category.name}


@app.post("/api/categories", status_code=status.HTTP_201_CREATED)
def create_category(payload: CategoryInput, db: DatabaseSession, _: AdminUser):
    category = Category(name=payload.name.strip())
    db.add(category)
    try:
        db.commit()
        db.refresh(category)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="A category with that name already exists") from exc
    return {"id": category.id, "name": category.name}


@app.put("/api/categories/{category_id}")
def update_category(category_id: int, payload: CategoryInput, db: DatabaseSession, _: AdminUser):
    category = db.get(Category, category_id)
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found")
    category.name = payload.name.strip()
    try:
        db.commit()
        db.refresh(category)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="A category with that name already exists") from exc
    return {"id": category.id, "name": category.name}


@app.delete("/api/categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_category(category_id: int, db: DatabaseSession, _: AdminUser):
    category = db.get(Category, category_id)
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found")
    db.delete(category)
    db.commit()


@app.get("/api/products")
def list_products(db: DatabaseSession, _: CurrentUser, search: str | None = None):
    query = select(Product).order_by(Product.name)
    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.where(
            Product.name.ilike(term)
            | Product.sku.ilike(term)
            | Product.barcode.ilike(term)
        )
    return [serialize_product(product) for product in db.scalars(query).all()]


@app.post("/api/products", status_code=status.HTTP_201_CREATED)
def create_product(payload: ProductInput, db: DatabaseSession, _: AdminUser):
    if payload.category_id is not None and db.get(Category, payload.category_id) is None:
        raise HTTPException(status_code=404, detail="Category not found")
    product = Product(**payload.model_dump())
    db.add(product)
    try:
        db.commit()
        db.refresh(product)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="SKU or barcode is already in use") from exc
    return serialize_product(product)


@app.get("/api/products/{product_id}")
def get_product(product_id: int, db: DatabaseSession, _: CurrentUser):
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    return serialize_product(product)


@app.put("/api/products/{product_id}")
def update_product(product_id: int, payload: ProductInput, db: DatabaseSession, _: AdminUser):
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    if payload.category_id is not None and db.get(Category, payload.category_id) is None:
        raise HTTPException(status_code=404, detail="Category not found")
    for key, value in payload.model_dump().items():
        setattr(product, key, value)
    try:
        db.commit()
        db.refresh(product)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="SKU or barcode is already in use") from exc
    return serialize_product(product)


@app.delete("/api/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_product(product_id: int, db: DatabaseSession, _: AdminUser):
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    if db.scalar(select(OrderItem.id).where(OrderItem.product_id == product_id).limit(1)) is not None:
        raise HTTPException(
            status_code=409,
            detail="Product is used by a completed order and cannot be deleted",
        )
    db.delete(product)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Product is used by a completed order and cannot be deleted",
        ) from exc


@app.get("/api/products/barcode/{barcode}")
def get_product_by_barcode(barcode: str, db: DatabaseSession, _: CurrentUser):
    product = db.scalar(select(Product).where(Product.barcode == barcode))
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    return serialize_product(product)


def serialize_order(order: Order):
    items = [
        {
            "product_id": item.product_id,
            "name": item.product_name,
            "sku": item.product_sku,
            "quantity": item.quantity,
            "unit_price": float(item.unit_price),
            "line_total": float(item.unit_price * item.quantity),
        }
        for item in order.items
    ]
    return {
        "id": order.id,
        "created_at": order.created_at.isoformat(),
        "subtotal": float(order.subtotal),
        "tax_rate": float(order.tax_rate),
        "tax_amount": float(order.tax_amount),
        "discount_type": order.discount_type,
        "discount_value": float(order.discount_value),
        "discount_amount": float(order.discount_amount),
        "total": float(order.total),
        "payment_method": order.payment_method,
        "cashier_id": order.cashier_id,
        "cashier_name": order.cashier.full_name if order.cashier else None,
        "items": items,
    }


@app.get("/api/orders")
def list_orders(db: DatabaseSession, _: AdminUser):
    orders = db.scalars(select(Order).order_by(Order.created_at.desc(), Order.id.desc())).all()
    return [serialize_order(order) for order in orders]


@app.post("/api/orders", status_code=status.HTTP_201_CREATED)
def create_order(payload: CheckoutRequest, db: DatabaseSession, cashier: CurrentUser):
    quantities: dict[int, int] = {}
    for item in payload.items:
        quantities[item.product_id] = quantities.get(item.product_id, 0) + item.quantity

    products = db.scalars(
        select(Product).where(Product.id.in_(quantities)).with_for_update()
    ).all()
    products_by_id = {product.id: product for product in products}
    if len(products_by_id) != len(quantities):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    for product_id, quantity in quantities.items():
        product = products_by_id[product_id]
        if product.stock_qty < quantity:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Insufficient stock for {product.name}",
            )

    subtotal = sum(
        (products_by_id[product_id].price * quantity for product_id, quantity in quantities.items()),
        start=Decimal("0.00"),
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if payload.discount_type == "percentage":
        if payload.discount_value > 100:
            raise HTTPException(status_code=422, detail="Percentage discount cannot exceed 100")
        discount_amount = subtotal * payload.discount_value / Decimal("100")
    else:
        discount_amount = payload.discount_value
    discount_amount = discount_amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if discount_amount > subtotal:
        raise HTTPException(status_code=422, detail="Discount cannot exceed the subtotal")
    taxable_amount = subtotal - discount_amount
    tax_amount = (taxable_amount * payload.tax_rate / Decimal("100")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    total = taxable_amount + tax_amount
    order = Order(
        subtotal=subtotal,
        tax_rate=payload.tax_rate,
        tax_amount=tax_amount,
        discount_type=payload.discount_type,
        discount_value=payload.discount_value,
        discount_amount=discount_amount,
        total=total,
        payment_method=payload.payment_method.strip(),
        cashier_id=cashier.id,
    )
    try:
        db.add(order)
        db.flush()
        for product_id, quantity in quantities.items():
            product = products_by_id[product_id]
            product.stock_qty -= quantity
            order.items.append(
                OrderItem(
                    product_id=product_id,
                    product_name=product.name,
                    product_sku=product.sku,
                    quantity=quantity,
                    unit_price=product.price,
                )
            )
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("Could not save POS order")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not complete the order",
        ) from exc

    db.refresh(order)
    return serialize_order(order)