"""
app.py
======
Flask Web Backend for Zomato Clone.
Built with Python, MySQL, and powered by Pandas & Matplotlib.

Clean user flow:
- Restaurant browsing, real-time search and category filtering (Pandas DataFrame)
- Cart calculations, GST tax and subtotal (Pandas Series)
- Order placement into local MySQL (zomato_db)
- User spending chart in My Orders (Matplotlib)
"""

import os
import secrets
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, session
import mysql.connector
from werkzeug.security import check_password_hash, generate_password_hash
from food_engine import (
    get_db, 
    filter_and_sort_restaurants, 
    calculate_cart_bill
)

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY') or secrets.token_hex(32)

# -------------------------------------------------------------
# 1. MAIN APP ROUTES
# -------------------------------------------------------------
@app.route('/')
def home():
    """Serves the main Zomato food delivery application."""
    return render_template('index.html')

@app.route('/orders')
def orders_page():
    """Serves the simple standalone order history page."""
    return render_template('orders.html')


@app.route('/login', methods=['GET', 'POST'])
def login_page():
    """Signs in an existing account or creates a customer account."""
    if request.method == 'GET':
        return render_template('login.html')

    action = request.form.get('action', 'login')
    email = request.form.get('email', '').strip().lower()
    password = request.form.get('password', '')
    if not email or not password:
        flash('Email and password are required.', 'error')
        return render_template('login.html', active_form=action)

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    try:
        if action == 'register':
            name = request.form.get('name', '').strip()
            phone = request.form.get('phone', '').strip()
            if not name:
                flash('Please enter your name.', 'error')
                return render_template('login.html', active_form='register')
            if len(password) < 8:
                flash('Password must be at least 8 characters long.', 'error')
                return render_template('login.html', active_form='register')
            cursor.execute('SELECT id FROM users WHERE email = %s LIMIT 1', (email,))
            if cursor.fetchone():
                flash('An account with this email already exists. Please sign in.', 'error')
                return render_template('login.html', active_form='register')
            cursor.execute(
                'INSERT INTO users (name, email, password_hash, phone) VALUES (%s, %s, %s, %s)',
                (name, email, generate_password_hash(password), phone or None)
            )
            conn.commit()
            user_id = cursor.lastrowid
            user_name = name
        else:
            cursor.execute(
                'SELECT id, name, password_hash FROM users WHERE email = %s LIMIT 1',
                (email,)
            )
            user = cursor.fetchone()
            if not user or not check_password_hash(user['password_hash'], password):
                flash('Email or password is incorrect.', 'error')
                return render_template('login.html', active_form='login')
            user_id = user['id']
            user_name = user['name']

        session['user_id'] = user_id
        session['user_name'] = user_name
        return redirect(url_for('home'))
    except mysql.connector.Error:
        conn.rollback()
        flash('Could not save or check this account. Make sure the users table is set up and the email is not already registered.', 'error')
        return render_template('login.html', active_form=action)
    finally:
        cursor.close()
        conn.close()


@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return redirect(url_for('home'))


# -------------------------------------------------------------
# 2. RESTAURANTS & MENU APIs (POWERED BY PANDAS DATAFRAME)
# -------------------------------------------------------------
@app.route('/api/restaurants', methods=['GET'])
def get_restaurants():
    """
    Fetches, searches, and filters restaurants using Pandas DataFrame engine.
    Query parameters:
    - search: search text for restaurant name or cuisine
    - category: 'all', 'veg', 'Biryani', 'Pizza', 'Burgers', 'rating', etc.
    - sort: 'rating' or 'price_for_two'
    """
    search_query = request.args.get('search', '').strip()
    category = request.args.get('category', 'all').strip()
    sort_by = request.args.get('sort', 'rating').strip()

    restaurants = filter_and_sort_restaurants(
        category=category,
        search_query=search_query,
        sort_by=sort_by,
        ascending=(sort_by == 'price_for_two')
    )
    return jsonify(restaurants)


@app.route('/api/restaurants/<int:restaurant_id>/menu', methods=['GET'])
def get_restaurant_menu(restaurant_id):
    """Fetches restaurant details and its menu items."""
    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("SELECT * FROM restaurants WHERE id = %s", (restaurant_id,))
    restaurant = cursor.fetchone()
    if restaurant and 'rating' in restaurant:
        restaurant['rating'] = float(restaurant['rating'])

    # database_setup.py creates `food_items` and stores the dish name in `name`.
    # Alias it to the field expected by the existing menu UI.
    cursor.execute("""
        SELECT id, restaurant_id, name AS item_name, category, price, is_veg,
               rating, description, image_url
        FROM food_items
        WHERE restaurant_id = %s
    """, (restaurant_id,))
    menu_items = cursor.fetchall()

    cursor.close()
    conn.close()

    return jsonify({
        'restaurant': restaurant,
        'menu': menu_items
    })


# -------------------------------------------------------------
# 3. CART CALCULATION API (POWERED BY PANDAS SERIES)
# -------------------------------------------------------------
@app.route('/api/cart/calculate', methods=['POST'])
def calculate_cart():
    """Calculates subtotal, delivery fee, GST tax using Pandas Series vector math."""
    data = request.get_json() or {}
    items = data.get('items', [])
    bill = calculate_cart_bill(items)
    return jsonify(bill)


# -------------------------------------------------------------
# 4. ORDER PLACEMENT & HISTORY (MYSQL + MATPLOTLIB)
# -------------------------------------------------------------
@app.route('/api/order', methods=['POST'])
def place_order():
    """Places an order and saves it into MySQL orders and order_items tables."""
    data = request.get_json() or {}
    items = data.get('items', [])
    if not items:
        return jsonify({'success': False, 'message': 'Cart is empty'}), 400

    customer_name = (data.get('name') or 'Customer').strip()
    customer_phone = (data.get('phone') or '9876543210').strip()
    customer_address = (data.get('address') or 'New Delhi').strip()
    payment_method = data.get('payment_method', 'Cash on Delivery')
    restaurant_id = data.get('restaurant_id', 1)

    # Calculate verified bill using Pandas Series engine
    bill = calculate_cart_bill(items)
    total_amount = bill['grand_total']

    conn = get_db()
    cursor = conn.cursor()
    try:
        # Persist orders using the columns actually created by database_setup.py.
        # The setup schema stores ordered dishes in one summary field and has no
        # separate users or order_items tables.
        items_summary = ', '.join(
            f"{item.get('name', 'Food Item')} (x{item.get('quantity', 1)})"
            for item in items
        )
        cursor.execute("""
            INSERT INTO orders
                (customer_name, customer_phone, delivery_address, items_summary,
                 total_amount, payment_mode, order_status)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (customer_name, customer_phone, customer_address, items_summary,
              total_amount, payment_method, 'Order Placed'))
        order_id = cursor.lastrowid
        conn.commit()
        return jsonify({
            'success': True,
            'message': 'Order placed successfully!',
            'order_id': order_id,
            'total_amount': total_amount,
            'estimated_delivery': '30-35 mins'
        })

    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        cursor.close()
        conn.close()


@app.route('/api/orders', methods=['GET'])
def get_orders():
    """Fetches user's order history from MySQL including item details."""
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT o.id, o.total_amount, o.delivery_address,
               o.customer_phone AS contact_phone,
               o.payment_mode AS payment_method, o.order_status,
               o.created_at AS order_date, NULL AS restaurant_name,
               o.items_summary
        FROM orders o
        ORDER BY o.id DESC
        LIMIT 20
    """)
    orders = cursor.fetchall()
    for o in orders:
        if o.get('order_date'):
            o['order_date'] = o['order_date'].strftime('%d %b %Y, %I:%M %p')

    cursor.close()
    conn.close()
    return jsonify(orders)


if __name__ == '__main__':
    print("=" * 55)
    print("  ZOMATO CLONE - USER WEB APP")
    print("  Running on: http://127.0.0.1:5000")
    print("=" * 55)
    app.run(debug=True, host='127.0.0.1', port=5000)
