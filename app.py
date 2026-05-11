from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import psycopg2
import psycopg2.extras
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'fashion_store_key_2026'


def get_db():
    try:
        conn = psycopg2.connect(
            dbname="Fashion_store",
            user="postgres",
            password="123",
            host="localhost",
            port="5433"
        )

        cur = conn.cursor()
        cur.execute("""
            SELECT current_database(),
                   current_schema(),
                   inet_server_addr(),
                   inet_server_port(),
                   version();
        """)
        print("DB INFO:", cur.fetchone())

        cur.execute("""
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_name = 'model';
        """)
        print("MODEL TABLE:", cur.fetchall())

        cur.close()
        return conn

    except Exception as e:
        print(f"DB Error: {e}")
        return None


# Прямой SQL-запрос вместо представления
CATALOG_QUERY = """
SELECT 
    m.model_id,
    p.name AS category,
    m.model_name,
    m.description,
    m.price,
    m.sizes,
    m.color,
    COALESCE(SUM(s.quantity), 0) AS in_stock
FROM model m
JOIN product p ON m.product_id = p.product_id
LEFT JOIN supply s ON m.model_id = s.model_id
GROUP BY m.model_id, p.name, m.model_name, m.description, m.price, m.sizes, m.color
"""


@app.route('/')
def index():
    conn = get_db()
    products = []
    if conn:
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(CATALOG_QUERY + " HAVING COALESCE(SUM(s.quantity), 0) > 0 LIMIT 6")
            products = cur.fetchall()
            cur.close()
        except Exception as e:
            print(f"Error: {e}")
        finally:
            conn.close()
    return render_template("index.html", products=products)


@app.route('/catalog')
def catalog():
    conn = get_db()
    products = []
    if conn:
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(CATALOG_QUERY + " HAVING COALESCE(SUM(s.quantity), 0) > 0 ORDER BY m.model_id")
            products = cur.fetchall()
            cur.close()
        except Exception as e:
            print(f"Error: {e}")
        finally:
            conn.close()
    return render_template("catalog.html", products=products)


@app.route('/product/<int:model_id>')
def product_detail(model_id):
    conn = get_db()
    if conn:
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(f"""
                SELECT * FROM ({CATALOG_QUERY}) AS catalog
                WHERE model_id = %s
            """, (model_id,))
            product = cur.fetchone()
            cur.close()
            conn.close()
            if product:
                return render_template("products.html", product=product)
        except Exception as e:
            print(f"Error: {e}")
    return "Product not found", 404


@app.route('/add_to_cart', methods=['POST'])
def add_to_cart():
    data = request.get_json()
    model_id = str(data['model_id'])
    if 'cart' not in session:
        session['cart'] = {}
    cart = session['cart']
    if model_id in cart:
        cart[model_id]['quantity'] += int(data['quantity'])
    else:
        cart[model_id] = {
            'name': data['name'],
            'price': float(data['price']),
            'quantity': int(data['quantity'])
        }
    session['cart'] = cart
    return jsonify({'status': 'ok', 'count': len(cart)})


@app.route('/cart')
def cart():
    items = []
    total = 0
    for mid, item in session.get('cart', {}).items():
        stotal = item['price'] * item['quantity']
        total += stotal
        items.append({**item, 'id': mid, 'total': stotal})
    return render_template("cart.html", cart_items=items, total=total)


@app.route('/checkout', methods=['GET', 'POST'])
def checkout():
    if request.method == 'GET':
        cart = session.get('cart', {})
        if not cart:
            return redirect('/catalog')
        total = sum(v['price'] * v['quantity'] for v in cart.values())
        return render_template("checkout.html", cart=cart, total=total)

    cart = session.get('cart', {})
    if not cart:
        return redirect('/catalog')

    conn = get_db()
    oid = None
    if conn:
        try:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO client (full_name, phone, email, delivery_address)
                VALUES (%s,%s,%s,%s)
                ON CONFLICT (phone) DO UPDATE SET full_name=EXCLUDED.full_name, email=EXCLUDED.email, delivery_address=EXCLUDED.delivery_address
                RETURNING client_id
            """, (request.form.get('name', ''), request.form.get('phone', ''),
                  request.form.get('email', ''), request.form.get('address', '')))
            cid = cur.fetchone()[0]

            total = sum(v['price'] * v['quantity'] for v in cart.values())
            cur.execute("""
                INSERT INTO client_order (client_id, order_date, status, total_amount, delivery_method)
                VALUES (%s,%s,'New',%s,%s) RETURNING client_order_id
            """, (cid, datetime.now().date(), total, request.form.get('delivery', 'Courier')))
            oid = cur.fetchone()[0]

            for mid, item in cart.items():
                cur.execute("""
                    INSERT INTO order_item (client_order_id, model_id, quantity, price_at_order)
                    VALUES (%s,%s,%s,%s)
                """, (oid, int(mid), item['quantity'], item['price']))

            conn.commit()
            cur.close()
        except Exception as e:
            print(f"Order error: {e}")
            conn.rollback()
        finally:
            conn.close()

    session.pop('cart', None)
    return render_template("order_success.html", order_id=oid or 0)


@app.route('/admin', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        conn = get_db()
        if conn:
            try:
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute("SELECT * FROM administrator WHERE login=%s AND password=%s",
                            (request.form['login'], request.form['password']))
                admin = cur.fetchone()
                cur.close()
                conn.close()
                if admin:
                    session['admin'] = True
                    session['admin_name'] = admin['full_name']
                    return redirect('/admin/orders')
                return render_template("admin_login.html", error="Wrong login or password")
            except Exception as e:
                print(f"Login error: {e}")
    return render_template("admin_login.html")


@app.route('/admin/logout')
def admin_logout():
    session.pop('admin', None)
    return redirect('/admin')


@app.route('/admin/orders')
def admin_orders():
    if not session.get('admin'):
        return redirect('/admin')
    conn = get_db()
    orders = []
    if conn:
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("""
                SELECT co.client_order_id, c.full_name AS client_name, c.phone, 
                       c.delivery_address, co.order_date, co.status, co.total_amount, 
                       co.delivery_method, e1.full_name AS packer, e2.full_name AS courier,
                       COUNT(oi.item_id) AS items_count
                FROM client_order co
                JOIN client c ON co.client_id = c.client_id
                LEFT JOIN employee e1 ON co.packer_id = e1.employee_id
                LEFT JOIN employee e2 ON co.courier_id = e2.employee_id
                LEFT JOIN order_item oi ON co.client_order_id = oi.client_order_id
                GROUP BY co.client_order_id, c.full_name, c.phone, c.delivery_address, 
                         co.order_date, co.status, co.total_amount, co.delivery_method, 
                         e1.full_name, e2.full_name
                ORDER BY co.client_order_id ASC
            """)
            orders = cur.fetchall()
            cur.close()
        except Exception as e:
            print(f"Error: {e}")
        finally:
            conn.close()
    return render_template("admin_orders.html", orders=orders)


@app.route('/admin/order/<int:order_id>', methods=['GET', 'POST'])
def admin_order_detail(order_id):
    if not session.get('admin'):
        return redirect('/admin')

    conn = get_db()
    if conn and request.method == 'POST':
        try:
            cur = conn.cursor()
            cur.execute("""
                UPDATE client_order SET packer_id=%s, courier_id=%s, status=%s
                WHERE client_order_id=%s
            """, (request.form.get('packer_id') or None, request.form.get('courier_id') or None,
                  request.form.get('status'), order_id))
            conn.commit()
            cur.close()
        except Exception as e:
            print(f"Update error: {e}")

    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT co.*, c.full_name AS client_name, c.phone, c.delivery_address,
               e1.full_name AS packer, e2.full_name AS courier
        FROM client_order co
        JOIN client c ON co.client_id=c.client_id
        LEFT JOIN employee e1 ON co.packer_id=e1.employee_id
        LEFT JOIN employee e2 ON co.courier_id=e2.employee_id
        WHERE co.client_order_id=%s
    """, (order_id,))
    order = cur.fetchone()

    cur.execute("""
        SELECT oi.*, m.model_name, m.color, m.sizes
        FROM order_item oi JOIN model m ON oi.model_id=m.model_id
        WHERE oi.client_order_id=%s
    """, (order_id,))
    items = cur.fetchall()

    cur.execute("SELECT * FROM employee WHERE status='Active'")
    employees = cur.fetchall()
    cur.close()
    conn.close()

    return render_template("admin_order_detail.html", order=order, items=items, employees=employees)


if __name__ == '__main__':
    app.run(debug=True)