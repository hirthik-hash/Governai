# backend/data/demo_data.py

"""
Expanded demo dataset (Day 75): 24 users and 20 resources across seven
departments, for the demo, the frontend, and end-to-end runs.

It EXTENDS the frozen seed data rather than editing it: DEMO_USERS
starts with exactly SEED_USERS and DEMO_RESOURCES with exactly
SEED_RESOURCES, then adds more. Hundreds of existing tests depend on the
exact seed users and resources (ids, names, chains), so SEED_* must not
change; new scenarios live here.

What the additions are there to exercise:
  - legal and operations departments (new cross-department cases);
  - an under-cleared manager in the middle of a chain (user-016), so the
    escalation agent's skip-and-keep-walking logic runs on real data;
  - a second blacklisted user (user-024);
  - fuzzy-name ambiguity: "report" (Q4 Financial Report / Vulnerability
    Scan Reports), "employee" (three resources), "database" (six);
  - every sensitivity level in several departments.
There is still exactly one root of the approval chain: the CISO, user-007.
"""

from data.seed_data import SEED_RESOURCES, SEED_USERS, Resource, Sensitivity, User

_EXTRA_USERS: list[User] = [
    User(id="user-011", name="Nadia Karim", department="legal", role="Legal Counsel", clearance_level=3, reports_to="user-012"),
    User(id="user-012", name="Oliver Grant", department="legal", role="General Counsel", clearance_level=4, reports_to="user-007"),
    User(id="user-013", name="Wei Zhang", department="security", role="Security Analyst", clearance_level=2, reports_to="user-014"),
    User(id="user-014", name="Fatima Al-Sayed", department="security", role="SOC Manager", clearance_level=3, reports_to="user-007"),
    User(id="user-015", name="Camila Torres", department="operations", role="Operations Coordinator", clearance_level=1, reports_to="user-016"),
    User(id="user-016", name="Ibrahim Yusuf", department="operations", role="Operations Manager", clearance_level=2, reports_to="user-017"),
    User(id="user-017", name="Hannah Berg", department="operations", role="VP Operations", clearance_level=4, reports_to="user-007"),
    User(id="user-018", name="Kenji Watanabe", department="engineering", role="Senior Engineer", clearance_level=2, reports_to="user-002"),
    User(id="user-019", name="Zoe Martin", department="engineering", role="DevOps Engineer", clearance_level=2, reports_to="user-002"),
    User(id="user-020", name="Ravi Menon", department="sales", role="Account Executive", clearance_level=1, reports_to="user-021"),
    User(id="user-021", name="Grace Liu", department="sales", role="Sales Director", clearance_level=3, reports_to="user-010"),
    User(id="user-022", name="Lucas Meyer", department="finance", role="Accountant", clearance_level=0, reports_to="user-004"),
    User(id="user-023", name="Amara Diallo", department="hr", role="Recruiter", clearance_level=0, reports_to="user-006"),
    User(id="user-024", name="Victor Alvarez", department="engineering", role="Contractor", clearance_level=1,
         is_blacklisted=True, reports_to="user-002"),
]

_EXTRA_RESOURCES: list[Resource] = [
    Resource(id="resource-009", name="Employee Benefits Guide", department="hr",
             sensitivity=Sensitivity.PUBLIC, resource_type="document"),
    Resource(id="resource-010", name="Legal Case Archive", department="legal",
             sensitivity=Sensitivity.RESTRICTED, resource_type="database"),
    Resource(id="resource-011", name="Contract Templates", department="legal",
             sensitivity=Sensitivity.INTERNAL, resource_type="document"),
    Resource(id="resource-012", name="Litigation Hold Register", department="legal",
             sensitivity=Sensitivity.TOP_SECRET, resource_type="database"),
    Resource(id="resource-013", name="Deployment Pipeline Config", department="engineering",
             sensitivity=Sensitivity.RESTRICTED, resource_type="system"),
    Resource(id="resource-014", name="Incident Response Playbook", department="security",
             sensitivity=Sensitivity.INTERNAL, resource_type="document"),
    Resource(id="resource-015", name="Vulnerability Scan Reports", department="security",
             sensitivity=Sensitivity.TOP_SECRET, resource_type="document"),
    Resource(id="resource-016", name="Vendor Payment Ledger", department="finance",
             sensitivity=Sensitivity.RESTRICTED, resource_type="database"),
    Resource(id="resource-017", name="Warehouse Inventory System", department="operations",
             sensitivity=Sensitivity.INTERNAL, resource_type="system"),
    Resource(id="resource-018", name="Operations Runbook", department="operations",
             sensitivity=Sensitivity.PUBLIC, resource_type="document"),
    Resource(id="resource-019", name="Sales Pipeline Dashboard", department="sales",
             sensitivity=Sensitivity.INTERNAL, resource_type="system"),
    Resource(id="resource-020", name="Customer Contact Database", department="sales",
             sensitivity=Sensitivity.RESTRICTED, resource_type="database"),
]

DEMO_USERS: list[User] = list(SEED_USERS) + _EXTRA_USERS
DEMO_RESOURCES: list[Resource] = list(SEED_RESOURCES) + _EXTRA_RESOURCES

# Who gets the API "admin" role in the development demo (Day 77): the CISO,
# the root of the approval chain. Production admins are assigned directly
# in the database - there is deliberately no endpoint that grants roles yet.
DEMO_ADMIN_USER_IDS: list[str] = ["user-007"]
