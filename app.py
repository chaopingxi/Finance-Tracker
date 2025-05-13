import os
import re
from flask import Flask, render_template, redirect, url_for, request, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, login_required, logout_user, current_user, UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import or_
from datetime import datetime
from functools import wraps
from flask_migrate import Migrate
from flask import make_response
from io import StringIO
import csv

# Tambahan untuk rate-limit
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

limiter = Limiter(
    get_remote_address,
    app=None,  # kita akan menginisialisasi dengan app nanti
    default_limits=["200 per day", "50 per hour"]
)

# ------------------------------
# Inisialisasi & Konfigurasi
# ------------------------------
app = Flask(__name__)
app.config['SECRET_KEY'] = 'supersecretkey'

basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'instance', 'flash.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join(basedir, 'static', 'uploads')

# Ensure upload directory exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.login_message_category = 'info'
login_manager.init_app(app)

migrate = Migrate(app, db)
# ------------------------------
# Models
# ------------------------------
# Model User
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    profile_image = db.Column(db.String(200), default='default.png')
    show_budget_warning = db.Column(db.Boolean, default=True)

    # Relasi ke kategori dan transaksi
    categories = db.relationship('Category', back_populates='user', lazy=True)
    transactions = db.relationship('Transaction', back_populates='user', lazy=True)

    # Sistem langganan
    subscription_level = db.Column(db.String(20), default='free')  # 'free', 'premium', 'ultra_premium'
    subscription_expiry = db.Column(db.DateTime, nullable=True)
    date_joined = db.Column(db.DateTime, default=datetime.utcnow)



# Model Category
class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    category_type = db.Column(db.String(10), nullable=False)
    budget_limit = db.Column(db.Float, nullable=True, default=0.0)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    # Hubungkan ke User
    user = db.relationship('User', back_populates='categories')
    transactions = db.relationship('Transaction', backref='category', lazy=True)

# Model Transaction
class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    amount = db.Column(db.Float, nullable=False)
    transaction_type = db.Column(db.String(10), nullable=False)
    note = db.Column(db.String(255), nullable=True)
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    from datetime import datetime
    ...
    date = db.Column(db.DateTime, default=datetime.utcnow)


    # Hubungkan ke User
    user = db.relationship('User', back_populates='transactions')


# ------------------------------
# Login Loader
# ------------------------------
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ------------------------------
# Decorators
# ------------------------------
def premium_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if (not current_user.is_authenticated or
            current_user.subscription_level not in ['premium', 'ultra_premium'] or
            not current_user.subscription_expiry or
            current_user.subscription_expiry < datetime.utcnow()):
            flash('Fitur ini membutuhkan langganan Premium aktif.', 'warning')
            return redirect(url_for('premium'))
        return f(*args, **kwargs)
    return decorated_function

def ultra_premium_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if (not current_user.is_authenticated or
            current_user.subscription_level != 'ultra_premium' or
            not current_user.subscription_expiry or
            current_user.subscription_expiry < datetime.utcnow()):
            flash('Fitur ini membutuhkan langganan Ultra Premium aktif.', 'warning')
            return redirect(url_for('ultra_premium'))
        return f(*args, **kwargs)
    return decorated_function
# ------------------------------
# Context Processors
# ------------------------------
@app.context_processor
def inject_current_year():
    return {'current_year': datetime.now().year}

# ------------------------------
# Routes: Auth
# ------------------------------
@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('home'))
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('home'))  # User udah login, langsung ke home

    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password = request.form['password']

        # Cek apakah email sudah terdaftar
        user_exists = User.query.filter_by(email=email).first()
        if user_exists:
            flash('Email sudah terdaftar', 'danger')
            return redirect(url_for('register'))

        # Hash password
        hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
        user = User(name=name, email=email, password=hashed_password)
        db.session.add(user)
        db.session.commit()

        # Auto login
        login_user(user)
        flash('Registrasi berhasil! Selamat datang di FinanceTracker', 'success')
        return redirect(url_for('home'))

    return render_template('register.html')

@limiter.limit("5 per 15 minutes")
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('home'))

    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        # Validasi input kosong
        if not email or not password:
            flash('Email dan password harus diisi.', 'warning')
            return redirect(url_for('login'))

        # Validasi format email
        if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
            flash('Format email tidak valid.', 'warning')
            return redirect(url_for('login'))

        # Cek apakah user ada
        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            flash(f'Selamat datang kembali, {user.name}!', 'success')
            next_page = request.args.get('next')
            return redirect(next_page) if next_page else redirect(url_for('home'))
        else:
            flash('Email atau password salah.', 'danger')

            # Optional: log untuk debugging saat development
            if not user:
                if app.config['DEBUG']:
                    print("Login gagal: email tidak ditemukan.")
            else:
                if app.config['DEBUG']:
                    print("Login gagal: password salah.")

    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Anda telah keluar dari akun', 'info')
    return redirect(url_for('login'))

# ------------------------------
# Routes: Home, Transaction, Category
# ------------------------------
@app.route('/home', methods=['GET', 'POST'])
@login_required
def home():
    search_query = request.args.get('q', '')
    categories = Category.query.filter_by(user_id=current_user.id).all()
    if search_query:
        transactions = Transaction.query.filter(
            Transaction.user_id == current_user.id,
            or_(
                Transaction.note.ilike(f"%{search_query}%"),
                Transaction.transaction_type.ilike(f"%{search_query}%")
            )
        ).order_by(Transaction.date.desc()).all()
    else:
        transactions = Transaction.query.filter_by(user_id=current_user.id).order_by(Transaction.date.desc()).all()

    total_income = sum(t.amount for t in transactions if t.transaction_type == 'income')
    total_expense = sum(t.amount for t in transactions if t.transaction_type == 'expense')
    total_balance = total_income - total_expense

    return render_template(
        'home.html',
        name=current_user.name,
        categories=categories,
        transactions=transactions,
        total_income=total_income,
        total_expense=total_expense,
        total_balance=total_balance,
        search_query=search_query,
        current_date=datetime.now()  # Added the current date
    )

@app.route('/transactions')
@login_required
def transactions():
    search_query = request.args.get('q', '')
    type_filter = request.args.get('type', '')
    category_filter = request.args.get('category', '')
    sort_option = request.args.get('sort', 'date_desc')
    
    # Basis query
    query = Transaction.query.filter_by(user_id=current_user.id)
    
    # Terapkan filter
    if search_query:
        query = query.filter(or_(
            Transaction.note.ilike(f"%{search_query}%"),
            Transaction.transaction_type.ilike(f"%{search_query}%")
        ))
    
    if type_filter:
        query = query.filter(Transaction.transaction_type == type_filter)
    
    if category_filter:
        query = query.filter(Transaction.category_id == category_filter)
    
    # Terapkan pengurutan
    if sort_option == 'date_asc':
        query = query.order_by(Transaction.date.asc())
    elif sort_option == 'amount_desc':
        query = query.order_by(Transaction.amount.desc())
    elif sort_option == 'amount_asc':
        query = query.order_by(Transaction.amount.asc())
    else:  # default: date_desc
        query = query.order_by(Transaction.date.desc())
    
    transactions = query.all()
    categories = Category.query.filter_by(user_id=current_user.id).all()
    
    return render_template(
        'transactions.html',
        transactions=transactions,
        categories=categories,
        current_date=datetime.now()
    )

@app.route('/add_category', methods=['POST'])
@login_required
def add_category():
    name = request.form['category_name']
    type_ = request.form['category_type']
    budget_limit = request.form.get('budget_limit')

    try:
        budget_limit = float(budget_limit) if budget_limit else 0.0
    except ValueError:
        budget_limit = 0.0

    existing_category = Category.query.filter_by(name=name, user_id=current_user.id).first()
    if existing_category:
        flash('Kategori dengan nama ini sudah ada', 'warning')
        return redirect(url_for('home'))

    new_category = Category(name=name, category_type=type_, user_id=current_user.id, budget_limit=budget_limit)
    db.session.add(new_category)
    db.session.commit()
    flash('Kategori berhasil ditambahkan', 'success')
    return redirect(url_for('home'))

@app.route('/add_transaction', methods=['GET', 'POST'])
@login_required
def add_transaction():
    if request.method == 'POST':
        try:
            # Ambil data dari form
            amount = request.form.get('amount')
            transaction_type = request.form.get('transaction_type')
            category_id = request.form.get('category_id')
            note = request.form.get('note', '')

            # Validasi input dasar
            if not amount or not transaction_type or not category_id:
                flash('Harap isi semua field yang wajib.', 'danger')
                return redirect(url_for('home'))

            # Konversi tipe data
            try:
                amount = float(amount)
                category_id = int(category_id)
            except ValueError:
                flash('Format jumlah atau kategori tidak valid.', 'danger')
                return redirect(url_for('home'))

            # Cek apakah kategori valid dan milik user
            category = Category.query.filter_by(id=category_id, user_id=current_user.id).first()
            if not category:
                flash("Kategori tidak ditemukan atau bukan milik Anda.", "danger")
                return redirect(url_for('home'))

            # Validasi anggaran jika kategori tipe expense
            if category.category_type == 'expense' and current_user.show_budget_warning:
                total_expense = sum(
                    t.amount for t in category.transactions if t.transaction_type == 'expense'
                )
                if total_expense + amount > category.budget_limit:
                    flash(f"⚠️ Pengeluaran ini akan melebihi anggaran kategori '{category.name}'!", 'warning')

            # Buat dan simpan transaksi
            transaction = Transaction(
                user_id=current_user.id,
                amount=amount,
                transaction_type=transaction_type,
                category_id=category_id,
                note=note,
                date=datetime.now()
            )
            db.session.add(transaction)
            db.session.commit()

            flash('Transaksi berhasil ditambahkan ✅', 'success')
            return redirect(url_for('home'))

        except Exception as e:
            db.session.rollback()
            import traceback
            print(traceback.format_exc())
            flash(f'Terjadi kesalahan: {str(e)}', 'danger')
            return redirect(url_for('home'))

    # Jika GET
    return redirect(url_for('home'))




@app.route('/edit_category/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_category(id):
    category = Category.query.get_or_404(id)
    
    # Verify ownership
    if category.user_id != current_user.id:
        flash('Anda tidak memiliki akses ke kategori ini', 'danger')
        return redirect(url_for('home'))
        
    if request.method == 'POST':
        category.name = request.form['name']
        category.category_type = request.form['category_type']
        db.session.commit()
        flash('Kategori berhasil diperbarui', 'success')
        return redirect(url_for('home'))
    return render_template('edit_category.html', category=category)

@app.route('/delete_category/<int:id>', methods=['POST'])
@login_required
def delete_category(id):
    category = Category.query.get_or_404(id)
    
    # Verify ownership
    if category.user_id != current_user.id:
        flash('Anda tidak memiliki akses ke kategori ini', 'danger')
        return redirect(url_for('home'))

    # Hapus semua transaksi terkait
    Transaction.query.filter_by(category_id=category.id).delete()

    db.session.delete(category)
    db.session.commit()
    flash('Kategori dan transaksi terkait berhasil dihapus', 'success')
    return redirect(url_for('home'))

@app.route('/edit_transaction/<int:id>', methods=['POST'])
@login_required
def edit_transaction(id):
    transaction = Transaction.query.get_or_404(id)
    
    # Verify ownership
    if transaction.user_id != current_user.id:
        flash('Anda tidak memiliki akses ke transaksi ini', 'danger')
        return redirect(url_for('home'))

    try:
        amount = float(request.form['amount'])
        if amount <= 0:
            flash("Jumlah harus lebih dari 0", "danger")
            return redirect(url_for('home'))
            
        transaction.amount = amount
        transaction.note = request.form.get('note', '')
        transaction.category_id = request.form.get('category_id', transaction.category_id)
        db.session.commit()
        flash('Transaksi berhasil diperbarui', 'success')
    except ValueError:
        flash("Jumlah harus berupa angka", "danger")
    except Exception as e:
        flash(f"Gagal memperbarui transaksi: {e}", "danger")
        
    return redirect(url_for('home'))

@app.route('/delete_transaction/<int:id>', methods=['POST'])
@login_required
def delete_transaction(id):
    transaction = Transaction.query.get_or_404(id)
    
    # Verify ownership
    if transaction.user_id != current_user.id:
        flash('Anda tidak memiliki akses ke transaksi ini', 'danger')
        return redirect(url_for('home'))
        
    db.session.delete(transaction)
    db.session.commit()
    flash('Transaksi berhasil dihapus', 'success')
    return redirect(url_for('home'))

# ------------------------------
# Routes: Charts, Profile, Settings
# ------------------------------
@app.route("/charts")
@login_required
def charts():
    categories = Category.query.filter_by(user_id=current_user.id).all()
    transactions = Transaction.query.filter_by(user_id=current_user.id).all()

    # Prepare chart data
    expense_data = {}
    income_data = {}
    
    for cat in categories:
        if cat.category_type == 'expense':
            expense_data[cat.name] = sum(t.amount for t in transactions if t.category_id == cat.id and t.transaction_type == 'expense')
        else:
            income_data[cat.name] = sum(t.amount for t in transactions if t.category_id == cat.id and t.transaction_type == 'income')

    # Combine expense and income data for charting
    chart_data = {**expense_data, **income_data}

    # Monthly data for line chart
    monthly_data = {}
    for transaction in transactions:
        month_key = transaction.date.strftime('%Y-%m')
        if month_key not in monthly_data:
            monthly_data[month_key] = {'income': 0, 'expense': 0}
        
        if transaction.transaction_type == 'income':
            monthly_data[month_key]['income'] += transaction.amount
        else:
            monthly_data[month_key]['expense'] += transaction.amount

    # Ensure all data is serializable to JSON (i.e., no None values)
    expense_data = {k: (v if v is not None else 0) for k, v in expense_data.items()}
    income_data = {k: (v if v is not None else 0) for k, v in income_data.items()}
    monthly_data = {k: {'income': (v['income'] if v['income'] is not None else 0),
                        'expense': (v['expense'] if v['expense'] is not None else 0)} 
                    for k, v in monthly_data.items()}

    return render_template(
        "charts.html", 
        expense_data=expense_data, 
        income_data=income_data,
        monthly_data=monthly_data,
        chart_data=chart_data  # Mengirimkan chart_data ke template
    )



@app.route('/export_csv')
@login_required
@premium_required
def export_csv():
    transactions = Transaction.query.filter_by(user_id=current_user.id).all()

    si = StringIO()
    writer = csv.writer(si)
    writer.writerow(['Tanggal', 'Jumlah', 'Tipe', 'Kategori', 'Catatan'])

    for t in transactions:
        writer.writerow([
            t.date.strftime('%Y-%m-%d'),
            t.amount,
            t.transaction_type,
            t.category.name,
            t.note or ''
        ])

    output = make_response(si.getvalue())
    output.headers["Content-Disposition"] = "attachment; filename=transaksi.csv"
    output.headers["Content-type"] = "text/csv"
    return output

@app.route('/premium')
@login_required
def premium():
    return render_template('premium.html')

@app.route('/ultra_premium')
@login_required
def ultra_premium():
    return render_template('ultra_premium.html')

from datetime import datetime, timedelta

@app.route('/upgrade/<level>', methods=['POST'])
@login_required
def upgrade(level):
    harga_paket = {
        'premium': 5000,
        'ultra_premium': 8000
    }

    if level not in harga_paket:
        flash('Paket tidak valid.', 'danger')
        return redirect(url_for('pricing'))

    # Simulasi pembayaran berhasil
    current_user.subscription_level = level
    current_user.subscription_expiry = datetime.utcnow() + timedelta(days=30)
    db.session.commit()

    flash(f'Berhasil upgrade ke {level.replace("_", " ").title()} sampai {current_user.subscription_expiry.strftime("%d-%m-%Y")}', 'success')
    return redirect(url_for('transactions'))


@app.route('/profile')
@login_required
def profile():
    user = current_user
    
    # Get transaction and category counts for stats
    transaction_count = Transaction.query.filter_by(user_id=current_user.id).count()
    category_count = Category.query.filter_by(user_id=current_user.id).count()
    
    # Calculate total savings
    transactions = Transaction.query.filter_by(user_id=current_user.id).all()
    total_income = sum(t.amount for t in transactions if t.transaction_type == 'income')
    total_expense = sum(t.amount for t in transactions if t.transaction_type == 'expense')
    savings = total_income - total_expense
    
    # Get recent transactions for the activity feed
    recent_transactions = Transaction.query.filter_by(user_id=current_user.id).order_by(
        Transaction.date.desc()).limit(5).all()
    
    return render_template(
        'profile.html', 
        user=user,
        transaction_count=transaction_count,
        category_count=category_count,
        savings=savings,
        recent_transactions=recent_transactions
    )

@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    if request.method == 'POST':
        current_user.show_budget_warning = 'show_budget_warning' in request.form
        db.session.commit()
        flash('Pengaturan berhasil disimpan!', 'success')
        return redirect(url_for('settings'))

    return render_template('settings.html')

@app.route('/edit_profile', methods=['GET', 'POST'])
@login_required
def edit_profile():
    user = current_user
    if request.method == 'POST':
        # Update profile data
        user.name = request.form['name']
        
        # Handle password change if provided
        if request.form.get('password') and request.form.get('password') == request.form.get('confirm_password'):
            user.password = generate_password_hash(request.form['password'], method='pbkdf2:sha256')
            
        # Handle profile image upload
        if 'profile_image' in request.files:
            file = request.files['profile_image']
            if file and file.filename:
                filename = secure_filename(file.filename)
                # Generate unique filename
                unique_filename = f"{current_user.id}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}"
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], unique_filename))
                user.profile_image = unique_filename
        
        db.session.commit()
        flash('Profil berhasil diperbarui', 'success')
        return redirect(url_for('profile'))
        
    return render_template('edit_profile.html', user=user)

@app.route('/about')
def about():
    return render_template('about.html')

# ------------------------------
# API Routes
# ------------------------------
@app.route('/api/transactions')
@login_required
def api_transactions():
    transactions = Transaction.query.filter_by(user_id=current_user.id).order_by(Transaction.date.desc()).all()
    result = []
    
    for t in transactions:
        result.append({
            'id': t.id,
            'amount': t.amount,
            'note': t.note,
            'type': t.transaction_type,
            'category': t.category.name,
            'date': t.date.strftime('%Y-%m-%d %H:%M:%S')
        })
        
    return jsonify(result)

@app.route('/api/categories')
@login_required
def api_categories():
    categories = Category.query.filter_by(user_id=current_user.id).all()
    result = []
    
    for c in categories:
        result.append({
            'id': c.id,
            'name': c.name,
            'type': c.category_type
        })
        
    return jsonify(result)

@app.route('/categories')
@login_required
def categories():
    categories = Category.query.filter_by(user_id=current_user.id).all()
    
    # Menghitung jumlah transaksi untuk setiap kategori
    for category in categories:
        category.transaction_count = Transaction.query.filter_by(category_id=category.id).count()
        
    # Memisahkan kategori berdasarkan tipe
    income_categories = [c for c in categories if c.category_type == 'income']
    expense_categories = [c for c in categories if c.category_type == 'expense']
    
    return render_template(
        'categories.html', 
        income_categories=income_categories,
        expense_categories=expense_categories
    )

# Tambahkan juga route untuk laporan karena ada di template
@app.route('/reports')
@login_required
def reports():
    return render_template('reports.html')

# ------------------------------
# Error Handlers
# ------------------------------
@app.errorhandler(404)
def not_found_error(error):
    return render_template('error/404.html'), 404

@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return render_template('error/500.html'), 500

@app.errorhandler(429)
def ratelimit_handler(e):
    # Flash message dan redirect ke halaman login
    flash("Terlalu banyak percobaan login, silakan coba lagi nanti.", "warning")
    return redirect(url_for('login')), 429

# ------------------------------
# Template Filters
# ------------------------------
@app.template_filter('currency')
def currency_format(value):
    try:
        return "Rp {:,.2f}".format(float(value)).replace(",", "X").replace(".", ",").replace("X", ".")
    except (ValueError, TypeError):
        return value

@app.template_filter('datetime')
def format_datetime(value, format='%d %b %Y %H:%M'):
    if value is None:
        return ""
    return value.strftime(format)

# ------------------------------
# Security Helpers
# ------------------------------
from itsdangerous import URLSafeTimedSerializer
serializer = URLSafeTimedSerializer(app.config['SECRET_KEY'])

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form['email']
        user = User.query.filter_by(email=email).first()
        if user:
            token = serializer.dumps(email, salt='reset-password')
            reset_link = url_for('reset_password', token=token, _external=True)

            # Kirim email (simulasi dulu)
            print(f"Link reset password: {reset_link}")  # nanti bisa kirim pakai Flask-Mail
            flash('Link reset password telah dikirim ke email kamu', 'info')
        else:
            flash('Email tidak ditemukan', 'danger')
    return render_template('forgot_password.html')

@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    try:
        email = serializer.loads(token, salt='reset-password', max_age=3600)
    except:
        flash('Link reset tidak valid atau telah kadaluarsa', 'danger')
        return redirect(url_for('login'))

    if request.method == 'POST':
        new_password = request.form['password']
        user = User.query.filter_by(email=email).first()
        if user:
            user.password = generate_password_hash(new_password)
            db.session.commit()
            flash('Password berhasil diubah. Silakan login', 'success')
            return redirect(url_for('login'))

    return render_template('reset_password.html')

from datetime import datetime

@app.context_processor
def inject_date():
    return {'current_date': datetime.now()}

@app.route('/api/add_transaction', methods=['POST'])
def api_add_transaction():
    if request.is_json:
        data = request.get_json()
        
        # Validate data
        if not data.get('amount') or not data.get('type') or not data.get('category'):
            return jsonify({'success': False, 'message': 'Missing required fields'})
        
        # Create transaction
        new_transaction = Transaction(
            amount=float(data.get('amount')),
            transaction_type=data.get('type'),
            category_id=data.get('category'),
            note=data.get('notes', '')
        )
        
        # Add to database
        db.session.add(new_transaction)
        db.session.commit()
        
        return jsonify({'success': True})
    
    return jsonify({'success': False, 'message': 'Invalid request format'})

    app.config['UPLOAD_FOLDER'] = os.path.join(basedir, 'static', 'uploads')
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

@app.route('/update_name', methods=['POST'])
@login_required
def update_name():
    new_name = request.form.get('name')

    if not new_name:
        flash('Nama tidak boleh kosong.', 'danger')
        return redirect(url_for('settings'))

    current_user.name = new_name
    db.session.commit()
    flash('Nama berhasil diperbarui.', 'success')
    return redirect(url_for('settings'))

@app.route('/update_password', methods=['POST'])
@login_required
def update_password():
    old_password = request.form.get('old_password')
    new_password = request.form.get('new_password')

    if not old_password or not new_password:
        flash('Semua field password wajib diisi.', 'danger')
        return redirect(url_for('settings'))

    if not check_password_hash(current_user.password, old_password):
        flash('Password lama salah.', 'danger')
        return redirect(url_for('settings'))

    current_user.password = generate_password_hash(new_password, method='pbkdf2:sha256')
    db.session.commit()
    flash('Password berhasil diperbarui.', 'success')
    return redirect(url_for('settings'))

@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings_page():
    if request.method == 'POST':
        current_user.show_budget_warning = 'show_budget_warning' in request.form
        db.session.commit()
        flash('Pengaturan berhasil disimpan!', 'success')
        return redirect(url_for('settings'))
    return render_template('settings.html')

from datetime import datetime, timedelta

@app.route('/subscribe/<level>', methods=['POST'])
@login_required
def subscribe(level):
    if level not in ['premium', 'ultra_premium']:
        flash('Pilihan langganan tidak valid.', 'danger')
        return redirect(url_for('settings'))

    # Harga (opsional, hanya untuk info pengguna)
    prices = {
        'premium': 5000,
        'ultra_premium': 8000
    }

    # Tambah 30 hari dari hari ini
    current_time = datetime.utcnow()
    new_expiry = current_time + timedelta(days=30)

    # Jika masih aktif, tambah dari expiry sebelumnya
    if current_user.subscription_expiry and current_user.subscription_expiry > current_time:
        new_expiry = current_user.subscription_expiry + timedelta(days=30)

    current_user.subscription_level = level
    current_user.subscription_expiry = new_expiry
    db.session.commit()

    flash(f'Langganan {level.replace("_", " ").title()} berhasil diperpanjang sampai {new_expiry.strftime("%Y-%m-%d")}.', 'success')
    return redirect(url_for('settings'))

@app.before_request
def check_subscription_expiry():
    if current_user.is_authenticated:
        if current_user.subscription_expiry and current_user.subscription_expiry < datetime.utcnow():
            current_user.subscription_level = 'free'
            db.session.commit()

# ------------------------------
# Import for file upload
# ------------------------------
from werkzeug.utils import secure_filename

# ------------------------------
# Main
# ------------------------------
if __name__ == '__main__':
    os.makedirs(os.path.join(basedir, 'instance'), exist_ok=True)
    with app.app_context():
        db.create_all()
    app.run(debug=True)