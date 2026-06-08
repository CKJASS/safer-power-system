import os
import io
import threading  # Added threading module to offload synchronous SMTP processes
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

if database_url:
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    basedir = os.path.abspath(os.path.dirname(__file__))
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'database.db')

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
# ------------------------------------------------------------

# Email Configuration (With automated key matching fallback logic)
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME') or os.environ.get('MAIL_USER')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD') or os.environ.get('MAIL_PASS')

db = SQLAlchemy(app)
mail = Mail(app)

ROLES = ['Employee', 'Supervisor', 'HR', 'Procurement', 'GM']

# ------------------ Database Models ------------------

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

# --- DATABASE INITIALIZATION & SECURE SEEDING BLOCK ---
with app.app_context():
    db.create_all()
    
    admin_user = User.query.filter_by(email='ckjass29@gmail.com').first()
    if not admin_user:
        hashed_password = generate_password_hash('saferpower2026', method='pbkdf2:sha256')
        new_user = User(
            name="Administrator",
            email="ckjass29@gmail.com",
            password=hashed_password,  
            role="HR"
        )
        db.session.add(new_user)
        
    test_sup = User.query.filter_by(email='supervisor@saferpower.com').first()
    if not test_sup:
        hashed_sup_pw = generate_password_hash('supervisor2026', method='pbkdf2:sha256')
        new_sup = User(
            name="Internal Supervisor",
            email="supervisor@saferpower.com",
            password=hashed_sup_pw,
            role="Supervisor"
        )
        db.session.add(new_sup)
        
    db.session.commit()
# ------------------------------------------------------------

@app.context_processor
def inject_now():
    return {'datetime_now': datetime.utcnow().strftime('%B %d, %Y')}

# ------------------ Helper Functions ------------------

def send_async_email(app_context, msg, recipient_email):
    """Background target runner that explicitly maps an isolated app context thread to send messages."""
    with app_context:
        try:
            mail.send(msg)
            print(f"Success: Notification dispatched cleanly to {recipient_email}")
        except Exception as e:
            print(f"SMTP Notification failure handled for {recipient_email}: {e}")

def send_email_notification(recipient_email, subject, body_text):
    """Initializes and forks off an independent application thread for emails to avoid blocking web workers."""
    try:
        from_email = app.config['MAIL_USERNAME']
        if not from_email:
            print("System Configuration Error: MAIL_USERNAME environment variable missing.")
            return

        msg = Message(subject, sender=from_email, recipients=[recipient_email])
        msg.body = body_text
        
        # Fork processing context over to an asynchronous task execution frame
        email_thread = threading.Thread(
            target=send_async_email,
            args=(app.app_context(), msg, recipient_email)
        )
        email_thread.start()
        
    except Exception as e:
        print(f"Failed to structuralize background email notification pipeline: {e}")

def route_to_next_approver(requisition, dashboard_url, active_sender_name):
    hierarchy = ['Supervisor', 'HR', 'Procurement', 'GM']
    try:
        previous_role = requisition.current_approver_role
        
        if requisition.current_approver_role not in hierarchy:
            requisition.current_approver_role = 'Supervisor'
            previous_role = 'Supervisor'
            
        current_index = hierarchy.index(requisition.current_approver_role)
        
        if current_index + 1 < len(hierarchy):
            next_role = hierarchy[current_index + 1]
            requisition.current_approver_role = next_role
            requisition.status = f'Pending {next_role} Approval'
            
            if requisition.requestor and requisition.requestor.email:
                send_email_notification(
                    requisition.requestor.email,
                    f"Requisition #{requisition.id} Approved by {previous_role}",
                    f"Hello {requisition.requestor.name},\n\nYour requisition #{requisition.id} has been verified and approved by {active_sender_name} ({previous_role}) and has moved to the {next_role} stage.\n\nTrack progress here: {dashboard_url}"
                )
            
            approvers = User.query.filter_by(role=next_role).all()
            if approvers:
                for approver in approvers:
                    if approver.email:
                        send_email_notification(
                            approver.email,
                            f"Action Required: Requisition #{requisition.id} Pending Approval",
                            f"Hello {approver.name},\n\nRequisition #{requisition.id} submitted by {requisition.requestor.name if requisition.requestor else 'Employee'} requires your review at the {next_role} stage.\n\nClick here to review and process: {dashboard_url}"
                        )
            else:
                print(f"Workflow Notice: Requisition advanced to {next_role}, but no users are registered under this role yet.")
        else:
            requisition.status = 'Approved'
            requisition.current_approver_role = 'None'
            
            if requisition.requestor and requisition.requestor.email:
                send_email_notification(
                    requisition.requestor.email,
                    f"Requisition #{requisition.id} Fully Approved!",
                    f"Great news {requisition.requestor.name},\n\nYour requisition #{requisition.id} has cleared final review parameters and is fully approved.\n\nView terminal details here: {dashboard_url}"
                )
        db.session.commit()
    except Exception as e:
        print(f"Exception encountered inside routing block: {e}")
        db.session.rollback()

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
                session['user_email'] = user.email
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
        reason = request.form.get('reason', '').strip()
        descriptions = request.form.getlist('description[]')
        quantities = request.form.getlist('quantity[]')
        costs = request.form.getlist('cost[]')
        
        if not descriptions or len(descriptions) == 0:
            flash('You must add at least one item.', 'danger')
            return redirect(url_for('new_requisition'))
            
        try:
            req = Requisition(requestor_id=session['user_id'], reason=reason)
            db.session.add(req)
            db.session.flush() 
            
            has_valid_items = False
            for desc, qty, cost in zip(descriptions, quantities, costs):
                if desc and desc.strip():
                    try:
                        parsed_qty = int(qty) if qty else 1
                    except ValueError:
                        parsed_qty = 1
                        
                    try:
                        clean_cost = str(cost).replace('KES', '').replace(',', '').strip()
                        parsed_cost = float(clean_cost) if clean_cost else 0.0
                    except ValueError:
                        parsed_cost = 0.0

                    item = RequisitionItem(
                        requisition_id=req.id,
                        description=desc.strip(),
                        quantity=parsed_qty,
                        estimated_cost=parsed_cost
                    )
                    db.session.add(item)
                    has_valid_items = True
            
            if not has_valid_items:
                db.session.rollback()
                flash('Requisition contains no valid item descriptions.', 'danger')
                return redirect(url_for('new_requisition'))
                
            db.session.commit()
            
        except Exception as e:
            db.session.rollback()
            print(f"Database insertion crash handled safely: {e}")
            flash('An error occurred while saving to the database database configuration.', 'danger')
            return redirect(url_for('dashboard'))
        
        dashboard_url = request.host_url.rstrip('/') + url_for('dashboard')
        supervisors = User.query.filter_by(role='Supervisor').all()
        if supervisors:
            for sup in supervisors:
                if sup.email:
                    send_email_notification(
                        sup.email,
                        "Action Required: New Requisition Generated",
                        f"Hello {sup.name},\n\nA new requisition entry #{req.id} has been submitted by {session['user_name']} requiring verification.\n\nOpen link to review asset parameters: {dashboard_url}"
                    )
        else:
            print("Workflow Notice: Requisition created, but no user with the role 'Supervisor' exists.")
            
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
    active_sender_name = session.get('user_name', 'An Approver')
        
    if action == 'approve':
        route_to_next_approver(req, dashboard_url, active_sender_name)
        flash(f'Requisition #{req.id} successfully approved and routed up the workflow line.', 'success')
    elif action == 'reject':
        previous_role = session["user_role"]
        req.status = f'Rejected by {previous_role}'
        req.current_approver_role = 'None'
        db.session.commit()
        
        if req.requestor and req.requestor.email:
            send_email_notification(
                req.requestor.email,
                f"Update: Requisition #{req.id} Rejected",
                f"Hello {req.requestor.name},\n\nYour requisition entry #{req.id} has been rejected by {active_sender_name} at the {previous_role} level.\n\nReview your dashboard link: {dashboard_url}"
            )
        flash(f'Requisition #{req.id} has been revoked.', 'warning')
        
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
                'Requestor Name': r.requestor.name if r.requestor else 'N/A',
                'Requestor Email': r.requestor.email if r.requestor else 'N/A',
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
    app.run(debug=True, host='0.0.0.0', port=5000)
