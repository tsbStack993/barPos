
# Barcode POS System

A secure, responsive, role-based Point of Sale (POS) web application built for fast retail checkouts, thermal receipt printing, and inventory management.

## 🚀 Tech Stack
* **Backend:** FastAPI (Python) with SQLAlchemy ORM
* **Frontend:** Tailwind CSS, responsive grid layouts, and HTML templates with dark mode support
* **Database:** SQLite (local development) / PostgreSQL / Neon (production cloud deployment)
* **Containerization:** Docker & Docker Compose

## ✨ Key Features
* **Barcode & Manual Product Search:** Instant product lookup via barcode SKU or a clickable visual grid.
* **Role-Based Access Control (RBAC):** 
  * **Admin:** Full access to product CRUD, category management, cashier provisioning, and sales history logs.
  * **Cashier:** Streamlined register terminal focused on checkout and receipt generation.
* **Dynamic Cart Calculations:** Adjustable tax percentages, flat/percentage discounts, and real-time subtotal/grand total computations.
* **Inventory Tracking:** Automatic stock deduction upon successful order completion with stock validation checks.
* **Receipt Generation:** Instant 80mm thermal-printer layout triggered automatically after checkout.
* **Dark Mode:** Seamless light and dark theme switching.

## 🛠️ Local Development (Docker)
1. Clone the repository:
```bash
git clone https://github.com/tsbStack993/barPos.git
cd barPos
   ```


2. Build and run using Docker Compose:
  
  ```bash
  docker-compose up --build
  ```


3. Open your browser and navigate to: `http://localhost:8000`
* **Default Admin Credentials:** `admin@pos.local` / `password123`



## ⚙️ Environment Variables

For production deployment (e.g., Render, Railway, or Neon), configure the following environment variables:

* `DATABASE_URL`: Your PostgreSQL connection string (e.g., Neon connection string with `?sslmode=require`)
* `SESSION_SECRET_KEY`: A secure random secret key for session management
* `SESSION_COOKIE_SECURE`: Set to `true` when serving over HTTPS
