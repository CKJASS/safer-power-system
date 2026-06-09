import os
import io
import threading
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_mail import Mail, Message
from werkzeug.security import generate_password_hash, check_password_hash
import pandas as pd
from datetime import datetime

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'safer_power_secret_key_default')

# ------------------ Database Configuration ------------------
database_url = os.environ.get('DATABASE_URL')
if database_url and database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)
app.config['SQLALCHEMY_DATABASE_URI'] = database_url or 'sqlite:///database.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# ------------------ SMTP Configuration ------------------
app.config['MAIL_SERVER'] = 'smtp-relay.brevo.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME') or os.environ.get('MAIL_USER')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD') or os.environ.get('MAIL_PASS')

db = SQLAlchemy(app)
mail = Mail(app)
ROLES = ['Employee', 'Supervisor', 'HR', 'Procurement', 'GM']

# ------------------ Models ------------------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(50), nullable=False)

class Requisition(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    requestor_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    reason = db.Column(db.Text, nullable=False)
    date_created = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(50), default='Pending Supervisor Approval')
    current_approver_role = db.Column(db.String(50), default='Supervisor')
    requestor = db.relationship('User', backref='requisitions', lazy='joined')
    items = db.relationship('RequisitionItem', backref='requisition', cascade="all, delete-orphan")

class RequisitionItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    requisition_id = db.Column(db.Integer, db.ForeignKey('requisition.id'), nullable=False)
    description = db.Column(db.String(250), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    estimated_cost = db.Column(db.Float, nullable=False)

with app.app_context():
    db.create_all()

# ------------------ Helpers ------------------
def send_email_notification(recipient_email, subject, body_text):
    msg = Message(subject, sender=app.config['MAIL_USERNAME'], recipients=[recipient_email])
    msg.body = body_text
    threading.Thread(target=lambda: mail.send(msg)).start()

def route_to_next_approver(requisition, dashboard_url, active_sender_name):
    hierarchy = ['Supervisor', 'HR', 'Procurement', 'GM']
    current_idx = hierarchy.index(requisition.current_approver_role)
    if current_idx + 1 < len(hierarchy):
        next_role = hierarchy[current_idx + 1]
        requisition.current_approver_role = next_role
        requisition.status = f'Pending {next_role} Approval'
    else:
        requisition.status = 'Approved'
        requisition.current_approver_role = 'None'
    db.session.commit()

# ------------------ Routes ------------------
@app.route('/')
def index():
    return redirect(url_for('dashboard') if 'user_id' in session else 'login')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        action = request.form.get('action')
        email = request.form.get('email')
        password = request.form.get('password')
        if action == 'register':
            user = User(name=request.form.get('name'), email=email, role=request.form.get('role'), password=generate_password_hash(password))
            db.session.add(user)
            db.session.commit()
            flash('Account created.', 'success')
        elif action == 'login':
            user = User.query.filter_by(email=email).first()
            if user and check_password_hash(user.password, password):
                session.update({'user_id': user.id, 'user_name': user.name, 'user_role': user.role, 'user_email': user.email})
                return redirect(url_for('dashboard'))
            flash('Invalid credentials.', 'danger')
    return render_template('login.html', roles=ROLES)

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session: return redirect(url_for('login'))
    reqs = Requisition.query.all() if session['user_role'] != 'Employee' else Requisition.query.filter_by(requestor_id=session['user_id']).all()
    return render_template('dashboard.html', requisitions=reqs, role=session['user_role'])

@app.route('/requisition/new', methods=['GET', 'POST'])
def new_requisition():
    if 'user_id' not in session: return redirect(url_for('login'))
    if request.method == 'POST':
        try:
            req = Requisition(requestor_id=session['user_id'], reason=request.form.get('reason'), status='Pending Supervisor Approval', current_approver_role='Supervisor')
            db.session.add(req)
            db.session.flush()
            for desc, qty, cost in zip(request.form.getlist('description[]'), request.form.getlist('quantity[]'), request.form.getlist('cost[]')):
                if desc.strip():
                    db.session.add(RequisitionItem(requisition_id=req.id, description=desc.strip(), quantity=int(qty or 1), estimated_cost=float(str(cost).replace('KES','').replace(',','') or 0)))
            db.session.commit()
            flash('Submitted successfully!', 'success')
            return redirect(url_for('dashboard'))
        except Exception:
            db.session.rollback()
            flash('Error saving requisition.', 'danger')
    return render_template('requisition.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(debug=True)
