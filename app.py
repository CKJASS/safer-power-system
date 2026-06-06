import os
import io
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_mail import Mail, Message
from werkzeug.security import generate_password_hash, check_password_hash
import pandas as pd
from datetime import datetime

app = Flask(__name__)
# Production fallback logic for secret key security
app.secret_key = os.environ.get('SECRET_KEY', 'safer_power_secret_key_default')

# ------------------ Database Configuration ------------------
database_url = os.environ.get('DATABASE_URL')

if database_url:
    # SQLAlchemy requires the prefix to be 'postgresql://' instead of 'postgres://'
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    # Local fallback for offline development
  import os

# Find the absolute folder path where app.py sits
basedir = os.path.abspath(os.path.dirname(__file__))

# Point the database directly inside that folder structure safely
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'database.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
# ------------------------------------------------------------

# Email Configuration
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USER')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASS')
app.config['MAIL_DEFAULT_SENDER'] = app.config['MAIL_USERNAME']

db = SQLAlchemy(app)
mail = Mail(app)

# Hierarchy Constants
ROLES = ['Employee', 'Supervisor', 'HR', 'Procurement', 'Finance', 'GM']

# ------------------ Database Models ------------------

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(50), nullable=False) # Employee, Supervisor, HR, etc.

class Requisition(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    requestor_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    reason = db.Column(db.Text, nullable=False)
    date_created = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(50), default='Pending Supervisor Approval')
    current_approver_role = db.Column(db.String(50), default='Supervisor')
    
    requestor = db.relationship('User', backref='requisitions')
    items = db.relationship('RequisitionItem', backref='requisition', cascade="all, delete-orphan")

class RequisitionItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    requisition_id = db.Column(db.Integer, db.ForeignKey('requisition.id'), nullable=False)
    description = db.Column(db.String(250), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    estimated_cost = db.Column(db.Float, nullable=False) # In KES

# ------------------ Context Processors ------------------

@app.context_processor
def inject_now():
    """Injects current date safely across UI views globally to prevent template crashes."""
    return {'datetime_now': datetime.utcnow().strftime('%B %d, %Y')}

# ------------------ Helper Functions ------------------

def send_email_notification(recipient_email, subject, body_text):
    try:
        msg = Message(subject, recipients=[recipient_email])
        msg.body = body_text
        mail.send(msg)
    except Exception as e:
        print(f"Failed to send email to {recipient_email}: {e}")

def route_to_next_approver(requisition, dashboard_url):
    hierarchy = ['Supervisor', 'HR', 'Procurement', 'Finance', 'GM']
    try:
        # Notify the requestor that their item progressed through the current level safely
        previous_role = requisition.current_approver_role
        
        current_index = hierarchy.index(requisition.current_approver_role)
        if current_index + 1 < len(hierarchy):
            next_role = hierarchy[current_index + 1]
            requisition.current_approver_role = next_role
            requisition.status = f'Pending {next_role} Approval'
            
            # 1. Notify the Requestor about progress at this level
            send_email_notification(
                requisition.requestor.email,
                f"Requisition #{requisition.id} Approved by {previous_role}",
                f"Hello {requisition.requestor.name},\n\nYour requisition #{requisition.id} has been approved by the {previous_role} tier and has moved to {next_role}.\n\nView details here: {dashboard_url}"
            )
            
            # 2. Notify next level approvers with direct review links
            approvers = User.query.filter_by(role=next_role).all()
            for approver in approvers:
                send_email_notification(
                    approver.email,
                    f"Action Required: Requisition #{requisition.id} Pending Approval",
                    f"Hello {approver.name},\n\nRequisition #{requisition.id} submitted by {requisition.requestor.name} requires your review.\n\nClick this link to access the dashboard and review: {dashboard_url}"
                )
        else:
            requisition.status = 'Approved'
            requisition.current_approver_role = 'None'
            
            # Final Level GM Approval Notification
            send_email_notification(
                requisition.requestor.email,
                f"Requisition #{requisition.id} Fully Approved!",
                f"Great news {requisition.requestor.name},\n\nYour requisition #{requisition.id} has been fully approved by the GM level.\n\nLink to view record: {dashboard_url}"
            )
    except ValueError:
        pass
    db.session.commit()

# ------------------ Routes ------------------

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        action = request.form.get('action')
        email = request.form.get('email')
        password = request.form.get('password')
        
        if action == 'register':
            name = request.form.get('name')
            role = request.form.get('role')
            if User.query.filter_by(email=email).first():
                flash('Email already registered!', 'danger')
                return redirect(url_for('login'))
            
            hashed_pw = generate_password_hash(password, method='pbkdf2:sha256')
            new_user = User(name=name, email=email, password=hashed_pw, role=role)
            db.session.add(new_user)
            db.session.commit()
            flash('Account created successfully! Please log in.', 'success')
            return redirect(url_for('login'))
            
        elif action == 'login':
            user = User.query.filter_by(email=email).first()
            if user and check_password_hash(user.password, password):
                session['user_id'] = user.id
                session['user_name'] = user.name
                session['user_role'] = user.role
                flash(f'Welcome back, {user.name}!', 'success')
                return redirect(url_for('dashboard'))
            else:
                flash('Invalid credentials. Check email and password.', 'danger')
                
        elif action == 'update_password':
            user = User.query.filter_by(email=email).first()
            if user:
                user.password = generate_password_hash(password, method='pbkdf2:sha256')
                db.session.commit()
                flash('Password updated successfully! Please log in with your new credentials.', 'success')
            else:
                flash('No account registered with that email address.', 'danger')
            return redirect(url_for('login'))
    
    return render_template('login.html', roles=ROLES)

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    user_role = session['user_role']
    user_id = session['user_id']
    
    if user_role == 'Employee':
        requisitions = Requisition.query.filter_by(requestor_id=user_id).all()
    else:
        requisitions = Requisition.query.all()

    return render_template('dashboard.html', requisitions=requisitions, role=user_role)

@app.route('/requisition/new', methods=['GET', 'POST'])
def new_requisition():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    if request.method == 'POST':
        reason = request.form.get('reason')
        descriptions = request.form.getlist('description[]')
        quantities = request.form.getlist('quantity[]')
        costs = request.form.getlist('cost[]')
        
        if not descriptions or len(descriptions) == 0:
            flash('You must add at least one item.', 'danger')
            return redirect(url_for('new_requisition'))
            
        req = Requisition(requestor_id=session['user_id'], reason=reason)
        db.session.add(req)
        db.session.flush() 
        
        for desc, qty, cost in zip(descriptions, quantities, costs):
            if desc.strip():
                item = RequisitionItem(
                    requisition_id=req.id,
                    description=desc,
                    quantity=int(qty),
                    estimated_cost=float(cost)
                )
                db.session.add(item)
                
        db.session.commit()
        
        # Build System Link Dynamically for emails
        dashboard_url = request.host_url.rstrip('/') + url_for('dashboard')
        
        # Notify initial workflow layer (Supervisors)
        supervisors = User.query.filter_by(role='Supervisor').all()
        for sup in supervisors:
            send_email_notification(
                sup.email,
                "New Requisition Pending Approval",
                f"Hello {sup.name},\n\nA new requisition #{req.id} has been submitted by {session['user_name']} and requires your review.\n\nClick here to open and process it: {dashboard_url}"
            )
            
        flash('Requisition submitted successfully!', 'success')
        return redirect(url_for('dashboard'))
        
    return render_template('requisition.html')

@app.route('/requisition/action/<int:req_id>/<string:action>')
def handle_action(req_id, action):
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    req = Requisition.query.get_or_404(req_id)
    
    if req.current_approver_role != session['user_role']:
        flash('You are not authorized to approve this at this stage.', 'danger')
        return redirect(url_for('dashboard'))
        
    dashboard_url = request.host_url.rstrip('/') + url_for('dashboard')
        
    if action == 'approve':
        route_to_next_approver(req, dashboard_url)
        flash(f'Requisition #{req.id} approved and routed.', 'success')
    elif action == 'reject':
        previous_role = session["user_role"]
        req.status = f'Rejected by {previous_role}'
        req.current_approver_role = 'None'
        db.session.commit()
        
        send_email_notification(
            req.requestor.email,
            f"Requisition #{req.id} Rejected",
            f"Hello {req.requestor.name},\n\nYour requisition #{req.id} has been rejected at the {previous_role} level.\n\nYou can review details here: {dashboard_url}"
        )
        flash(f'Requisition #{req.id} has been rejected.', 'warning')
        
    return redirect(url_for('dashboard'))

@app.route('/requisition/delete/<int:req_id>')
def delete_requisition(req_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    req = Requisition.query.get_or_404(req_id)
    if req.requestor_id == session['user_id'] or session['user_role'] == 'GM':
        db.session.delete(req)
        db.session.commit()
        flash(f'Requisition #{req_id} has been successfully canceled and removed.', 'success')
    else:
        flash('You are not authorized to delete this requisition entry.', 'danger')
        
    return redirect(url_for('dashboard'))

@app.route('/delete-account', methods=['POST'])
def delete_account():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    user = User.query.get(session['user_id'])
    if user:
        db.session.delete(user)
        db.session.commit()
        session.clear()
        flash('Your profile identity ledger data has been permanently cleared.', 'info')
        
    return redirect(url_for('login'))

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out successfully.', 'info')
    return redirect(url_for('login'))

@app.route('/export/excel')
def export_excel():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    requisitions = Requisition.query.all()
    data = []
    
    for r in requisitions:
        for item in r.items:
            data.append({
                'Requisition ID': r.id,
                'Requestor Name': r.requestor.name,
                'Requestor Email': r.requestor.email,
                'Reason': r.reason,
                'Date Created': r.date_created.strftime('%Y-%m-%d %H:%M'),
                'Item Description': item.description,
                'Quantity': item.quantity,
                'Cost (KES)': item.estimated_cost,
                'Total Cost (KES)': item.quantity * item.estimated_cost,
                'Status': r.status
            })
            
    df = pd.DataFrame(data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Requisitions')
    output.seek(0)
    
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f"Safer_Power_Requisitions_{datetime.now().strftime('%Y%m%d')}.xlsx"
    )

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        
        # --- ADD THIS CODE TO CREATE AN INITIAL USER ---
        # Checks if your email is already in the database
        admin_user = User.query.filter_by(email='ckjass29@gmail.com').first()
        
        if not admin_user:
            # Creates the user if the database is empty
            new_user = User(
                name="Administrator",
                email="ckjass29@gmail.com",
                password="your_password_here",  # Put the password you want to use here
                role="Admin"                    # Make sure this matches your model roles
            )
            db.session.add(new_user)
            db.session.commit()
        # -----------------------------------------------

    app.run(debug=True, host='0.0.0.0', port=5000)
