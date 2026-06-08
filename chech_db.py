from app import app, db, User, Requisition, RequisitionItem

with app.app_context():
    print("\n=========================================")
    print("   SAFER POWER POSTGRESQL LIVE LEDGER    ")
    print("=========================================\n")
    
    # 1. Pull Users
    print("[1] REGISTERED USERS:")
    users = User.query.all()
    if not users:
        print(" -> No users found in PostgreSQL database yet.")
    for u in users:
        print(f"  • ID: {u.id:^3} | Name: {u.name:<15} | Role: {u.role:<12} | Email: {u.email}")
        
    print("\n" + "-"*50 + "\n")
    
    # 2. Pull Requisitions
    print("[2] ACTIVE REQUISITIONS:")
    reqs = Requisition.query.all()
    if not reqs:
        print(" -> No requisition data logged yet.")
    for r in reqs:
        requestor_name = r.requestor.name if r.requestor else "Unknown"
        print(f"  • REQ #{r.id} | From: {requestor_name:<12} | Status: {r.status}")
        # List items within this requisition
        for item in r.items:
            print(f"    └─ Item: {item.description:<20} | Qty: {item.quantity} | Cost: KES {item.estimated_cost:,.2f}")
            
    print("\n=========================================")
