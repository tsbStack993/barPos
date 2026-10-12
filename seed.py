import os
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from main import Base, Category, Product, SessionLocal, User, engine


SAMPLE_CATEGORIES = ("Beverages", "Bakery", "Snacks", "Produce")


SAMPLE_PRODUCTS = (
    {
        "name": "Coffee Beans 500g",
        "sku": "COF-500",
        "barcode": "0123456789012",
        "price": Decimal("12.99"),
        "stock_qty": 25,
        "category": "Beverages",
    },
    {
        "name": "Whole Milk 1L",
        "sku": "MLK-001",
        "barcode": "0123456789029",
        "price": Decimal("2.49"),
        "stock_qty": 40,
        "category": "Beverages",
    },
    {
        "name": "Sourdough Bread",
        "sku": "BRD-SOUR",
        "barcode": "0123456789036",
        "price": Decimal("4.25"),
        "stock_qty": 18,
        "category": "Bakery",
    },
    {
        "name": "Dark Chocolate Bar",
        "sku": "CHO-DARK",
        "barcode": "0123456789043",
        "price": Decimal("3.50"),
        "stock_qty": 30,
        "category": "Snacks",
    },
    {
        "name": "Apple (each)",
        "sku": "APL-EACH",
        "barcode": "0123456789050",
        "price": Decimal("0.75"),
        "stock_qty": 60,
        "category": "Produce",
    },
)


def seed_products(db: Session) -> int:
    """Create starter categories and insert sample products only into an empty catalog."""
    categories = {
        category.name: category
        for category in db.scalars(select(Category)).all()
    }
    for name in SAMPLE_CATEGORIES:
        categories.setdefault(name, Category(name=name))
    db.add_all(category for category in categories.values() if category.id is None)
    db.flush()

    if db.scalar(select(Product.id).limit(1)) is not None:
        for product_data in SAMPLE_PRODUCTS:
            product = db.scalar(select(Product).where(Product.sku == product_data["sku"]))
            if product is not None and product.category_id is None:
                product.category = categories[product_data["category"]]
        db.commit()
        return 0

    db.add_all(
        Product(
            **{key: value for key, value in product.items() if key != "category"},
            category=categories[product["category"]],
        )
        for product in SAMPLE_PRODUCTS
    )
    db.commit()
    return len(SAMPLE_PRODUCTS)


def seed_admin(db: Session) -> bool:
    """Create the initial administrator only when the user table is empty."""
    if db.scalar(select(User.id).limit(1)) is not None:
        return False

    email = os.getenv("INITIAL_ADMIN_EMAIL", "admin@pos.local").strip().lower()
    password = os.getenv("INITIAL_ADMIN_PASSWORD", "password123")
    admin = User(email=email, full_name="POS Administrator", role="admin")
    admin.set_password(password)
    db.add(admin)
    db.commit()
    return True


if __name__ == "__main__":
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as session:
        inserted = seed_products(session)
        admin_created = seed_admin(session)
    print(f"Seeded {inserted} products." if inserted else "Products already exist; nothing seeded.")
    print("Seeded default administrator." if admin_created else "Users already exist; administrator unchanged.")
