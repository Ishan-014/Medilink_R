# MediLink

MediLink is a role-based healthcare workflow prototype built using React, FastAPI, and PostgreSQL.

The project implements authentication, Role-Based Access Control (RBAC), synthetic EHR records, separate Doctor and Receptionist workflows, patient search, patient detail pages, a role-aware MediLink Assistant, and PDF/TXT document upload.

This is an academic/demo system using synthetic data only. It is not intended for real patient or production healthcare use.



- Python
- FastAPI
- Uvicorn
- PyJWT
- pwdlib / Argon2
- python-multipart
- pypdf

Database:

- PostgreSQL
- psycopg2


============================================================
3. SYSTEM ARCHITECTURE
============================================================

The current architecture is:

MediLink Frontend
       |
       |
       v
React + React Router
       |
       |
JWT Bearer Authentication
       |
       |
       v
FastAPI Backend
       |
       |
       +----------------------+
       |                      |
       v                      v
Doctor Access          Receptionist Access
       |                      |
       |                      |
       +----------+-----------+
                  |
                  v
              PostgreSQL
                  |
                  v
          Synthetic Mock EHR


The FastAPI backend is the main security layer.

The frontend may hide unavailable information, but access control is enforced by the backend using the authenticated user's JWT role.


============================================================
4. USER ROLES
============================================================

There are currently two roles:

1. Doctor
2. Receptionist


------------------------------------------------------------
DOCTOR ROLE
------------------------------------------------------------

Doctors can access clinical patient information such as:

- Patient ID
- First name
- Last name
- Date of birth
- Gender
- Blood group
- Assigned doctor
- Department
- Last visit
- Diagnosis summary
- Medications
- Allergies
- Lab summary

Doctors can also use MediLink Assistant to ask clinical questions.


------------------------------------------------------------
RECEPTIONIST ROLE
------------------------------------------------------------

Receptionists can access administrative patient information such as:

- Patient ID
- First name
- Last name
- Date of birth
- Gender
- Phone
- Email
- Address
- Emergency contact
- Insurance provider
- Policy number
- Assigned doctor
- Department
- Next appointment
- Appointment status

Receptionists cannot access:

- Diagnosis
- Medication
- Allergies
- Lab results
- Clinical information

These restrictions are enforced by the FastAPI backend.


============================================================
5. DEMO LOGIN ACCOUNTS
============================================================

Doctor account:

Email:
doctor@medilink.demo

Password:
doctor123

Role:
doctor


Receptionist account:

Email:
reception@medilink.demo

Password:
reception123

Role:
receptionist


Passwords are stored as hashes in PostgreSQL.


============================================================
6. DATABASE INFORMATION
============================================================

Database engine:

PostgreSQL

Database name:

medilink

Default PostgreSQL port:

5432

Local development host:

localhost

or:

127.0.0.1


Important database objects:

- patients
- users
- doctor_patient_view
- receptionist_patient_view


============================================================
7. PATIENTS TABLE
============================================================

The patients table contains approximately 1000 synthetic patient records.

Patient IDs follow this format:

MED00001
MED00002
MED00003
...
MED01000


Important columns:

patient_id
first_name
last_name
date_of_birth
gender
blood_group
phone
email
address
emergency_contact
insurance_provider
policy_number
assigned_doctor
department
last_visit
next_appointment
appointment_status
diagnosis_summary
medications
allergies
lab_summary
is_synthetic
created_at


Primary key:

patient_id


============================================================
8. USERS TABLE
============================================================

The users table contains application users.

Important columns:

user_id
full_name
email
password_hash
role
is_active
created_at


Allowed roles:

doctor
receptionist


============================================================
9. DOCTOR DATABASE VIEW
============================================================

Database view:

doctor_patient_view


This view exposes clinical patient information.

Fields include:

patient_id
first_name
last_name
date_of_birth
gender
blood_group
assigned_doctor
department
last_visit
diagnosis_summary
medications
allergies
lab_summary


============================================================
10. RECEPTIONIST DATABASE VIEW
============================================================

Database view:

receptionist_patient_view


This view exposes only administrative information.

Fields include:

patient_id
first_name
last_name
date_of_birth
gender
phone
email
address
emergency_contact
insurance_provider
policy_number
assigned_doctor
department
next_appointment
appointment_status


Clinical fields are intentionally excluded.


============================================================
11. BACKEND
============================================================

Backend framework:

FastAPI


Local backend address:

http://127.0.0.1:8000


Swagger API documentation:

http://127.0.0.1:8000/docs


Main backend file:

backend/main.py


============================================================
12. BACKEND API ENDPOINTS
============================================================

Authentication:

POST /login

Current user:

GET /me


Doctor endpoints:

GET /doctor/patients

GET /doctor/patient/{patient_id}


Receptionist endpoints:

GET /receptionist/patients

GET /receptionist/patient/{patient_id}


MediLink Assistant:

POST /chat


Document upload:

POST /chat/upload


============================================================
13. LOGIN API
============================================================

Endpoint:

POST /login


Example request:

{
  "email": "doctor@medilink.demo",
  "password": "doctor123"
}


Example response:

{
  "message": "Login successful",
  "access_token": "<JWT_TOKEN>",
  "token_type": "bearer",
  "user": {
    "user_id": 1,
    "full_name": "Dr. Demo",
    "email": "doctor@medilink.demo",
    "role": "doctor"
  }
}


Protected API requests must include:

Authorization: Bearer <JWT_TOKEN>


The JWT contains:

user_id
email
role
expiration


============================================================
14. GET CURRENT USER
============================================================

Endpoint:

GET /me


This endpoint returns the currently authenticated user information.

Example:

{
  "user_id": 1,
  "email": "doctor@medilink.demo",
  "role": "doctor"
}


============================================================
15. DOCTOR API
============================================================

Doctor patient list:

GET /doctor/patients


Doctor patient search:

GET /doctor/patients?q=MED00001


Example:

GET /doctor/patients?q=Cardiology


Individual Doctor patient:

GET /doctor/patient/MED00001


These endpoints require:

role = doctor


A Receptionist token attempting to access Doctor-only endpoints receives:

403 Forbidden


============================================================
16. RECEPTIONIST API
============================================================

Receptionist patient list:

GET /receptionist/patients


Search example:

GET /receptionist/patients?q=MED00001


Individual Receptionist patient:

GET /receptionist/patient/MED00001


These endpoints require:

role = receptionist


The Receptionist patient response contains administrative information only.


============================================================
17. MEDILINK ASSISTANT
============================================================

Endpoint:

POST /chat


Example request:

{
  "message": "What is the diagnosis of MED00001?"
}


The logged-in user's JWT determines what information can be returned.


Doctor examples:

What is the diagnosis of MED00001?

What medication is MED00001 taking?

What allergies does MED00001 have?

What is the lab summary of MED00001?

What is the blood group of MED00001?

When was MED00001's last visit?


Receptionist examples:

When is MED00001's next appointment?

What insurance does MED00001 have?

What is MED00001's phone number?

What is MED00001's email?

Which doctor is assigned to MED00001?


If a Receptionist asks:

What medication is MED00001 taking?


The backend blocks the request and returns:

403 Forbidden


The current MediLink Assistant is rule-based/deterministic.

It currently performs:

- Role-aware EHR lookup
- Patient information lookup
- Simple document summary
- Simple document text search

It is not yet a full external LLM integration.


============================================================
18. DOCUMENT UPLOAD
============================================================

Endpoint:

POST /chat/upload


Authentication:

Authorization: Bearer <JWT_TOKEN>


Upload method:

multipart/form-data


File field:

file


Supported file types:

.pdf
.txt


Maximum file size:

5 MB


PDF extraction uses:

pypdf


TXT extraction uses:

UTF-8 decoding


Example successful response:

{
  "message": "Document uploaded and processed successfully.",
  "filename": "report.pdf",
  "file_type": "PDF",
  "characters_extracted": 3825,
  "preview": "..."
}


============================================================
19. DOCUMENT STORAGE LIMITATION
============================================================

Uploaded document text is currently stored in FastAPI memory.

Current prototype storage:

uploaded_documents = {}


Therefore:

If FastAPI restarts, uploaded document context is lost.


This is acceptable for the prototype/demo version.

Future improvement:

- PostgreSQL document table
- File/object storage
- Persistent document sessions


============================================================
20. PDF LIMITATION
============================================================

Normal text-based PDFs are supported.

Scanned image-only PDFs are not currently OCR processed.

Future enhancement:

OCR support for scanned PDFs and images.


============================================================
21. FRONTEND ROUTES
============================================================

Login:

/


Doctor Dashboard:

/doctor


Doctor Patient Detail:

/doctor/patient/:patientId


Receptionist Dashboard:

/receptionist


Receptionist Patient Detail:

/receptionist/patient/:patientId


MediLink AI Workspace:

/ai


============================================================
22. FRONTEND DESIGN
============================================================

The frontend uses a modern minimalist healthcare SaaS design.

Main design characteristics:

- Fixed sidebar navigation
- Healthcare teal color palette
- White cards
- Soft lavender/gray background
- Role-specific portals
- Patient tables
- Status badges
- Patient detail cards
- MediLink AI workspace
- Responsive layout


The sidebar includes:

- Dashboard
- Patients
- MediLink AI
- Appointments
- Sign Out


============================================================
23. FRONTEND STRUCTURE
============================================================

Typical frontend structure:

frontend/
|
|-- src/
|   |
|   |-- components/
|   |   |
|   |   |-- AppShell.jsx
|   |   |-- Sidebar.jsx
|   |   |-- Topbar.jsx
|   |   |-- StatCard.jsx
|   |   |-- StatusBadge.jsx
|   |   `-- Chatbot.jsx
|   |
|   |-- pages/
|   |   |
|   |   |-- Login.jsx
|   |   |-- DoctorDashboard.jsx
|   |   |-- ReceptionistDashboard.jsx
|   |   |-- DoctorPatient.jsx
|   |   |-- ReceptionistPatient.jsx
|   |   `-- AIWorkspace.jsx
|   |
|   |-- App.jsx
|   |-- main.jsx
|   `-- index.css
|
|-- package.json
|-- package-lock.json
`-- ...


============================================================
24. BACKEND STRUCTURE
============================================================

Typical backend structure:

backend/
|
|-- main.py
|-- requirements.txt
`-- venv/


Important:

Do not push the venv folder to GitHub.


============================================================
25. RUNNING POSTGRESQL
============================================================

Check which PostgreSQL version is installed:

brew list | grep postgresql


Example output:

postgresql@16


Start PostgreSQL:

brew services start postgresql@16


Use the version actually installed on your machine.


Open the MediLink database:

psql medilink


Disable pager:

\pset pager off


Show tables:

\dt


Check patient count:

SELECT COUNT(*) FROM patients;


Expected:

1000


Show application users:

SELECT user_id, full_name, email, role
FROM users;


Show Doctor view:

SELECT *
FROM doctor_patient_view
LIMIT 5;


Show Receptionist view:

SELECT *
FROM receptionist_patient_view
LIMIT 5;


Exit PostgreSQL:

\q


============================================================
26. RUNNING THE BACKEND
============================================================

Open a terminal.

Run:

cd ~/medilink/backend


Activate Python virtual environment:

source venv/bin/activate


Start FastAPI:

uvicorn main:app --reload


Backend will run at:

http://127.0.0.1:8000


Swagger:

http://127.0.0.1:8000/docs


============================================================
27. RUNNING THE FRONTEND
============================================================

Open another terminal.

Run:

cd ~/medilink/frontend


Install frontend dependencies if required:

npm install


Start React/Vite:

npm run dev


Frontend will normally run at:

http://localhost:5173


============================================================
28. NORMAL DEVELOPMENT SETUP
============================================================

Terminal 1:

PostgreSQL


brew services start postgresql@16

psql medilink


Terminal 2:

FastAPI


cd ~/medilink/backend

source venv/bin/activate

uvicorn main:app --reload


Terminal 3:

React


cd ~/medilink/frontend

npm run dev


============================================================
29. BACKEND DEPENDENCIES
============================================================

Important Python packages:

fastapi
uvicorn
psycopg2-binary
PyJWT
pwdlib[argon2]
python-multipart
pypdf


Install manually:

pip install fastapi uvicorn psycopg2-binary PyJWT "pwdlib[argon2]" python-multipart pypdf


Generate requirements.txt:

cd ~/medilink/backend

source venv/bin/activate

pip freeze > requirements.txt


Another developer can then install the backend dependencies using:

pip install -r requirements.txt


============================================================
30. FRONTEND DEPENDENCIES
============================================================

Install dependencies:

cd frontend

npm install


The redesigned UI uses:

lucide-react


Install if required:

npm install lucide-react


============================================================
31. ENVIRONMENT VARIABLES
============================================================

For the final integrated system, database credentials and JWT secrets should be stored in environment variables.

Recommended .env example:

DB_HOST=localhost
DB_PORT=5432
DB_NAME=medilink
DB_USER=medilink_app
DB_PASSWORD=YOUR_DATABASE_PASSWORD

JWT_SECRET=YOUR_LONG_RANDOM_SECRET
JWT_ALGORITHM=HS256


Important:

Do not commit a real .env file containing passwords or secrets.


Instead, create:

.env.example


Example:

DB_HOST=localhost
DB_PORT=5432
DB_NAME=medilink
DB_USER=
DB_PASSWORD=

JWT_SECRET=
JWT_ALGORITHM=HS256


============================================================
32. RECOMMENDED .gitignore
============================================================

Create a file named:

.gitignore


Place it in the project root.


Use:

.DS_Store

# Secrets
.env
.env.*
!.env.example

# Python
backend/venv/
venv/
__pycache__/
*.pyc
*.pyo

# React / Node
frontend/node_modules/
node_modules/
frontend/dist/
dist/

# Logs
*.log

# Temporary files
*.tmp
*.swp


============================================================
33. WHAT SHOULD NOT BE PUSHED TO GITHUB
============================================================

Do not push:

.env

backend/venv/

frontend/node_modules/

database passwords

real JWT secrets

API keys

real patient information

private credentials


============================================================
34. WHAT IS SAFE TO PUSH
============================================================

Safe files include:

backend/main.py

backend/requirements.txt

frontend/src/

frontend/package.json

frontend/package-lock.json

README.md

.gitignore

.env.example

database setup scripts

synthetic-data scripts

CSS

React components

React pages


============================================================
35. PUSHING TO GITHUB
============================================================

Go to GitHub.

Create a new repository.

Example repository name:

medilink


Do not create a README on GitHub if you already have one locally.


In Terminal:

cd ~/medilink


Initialize Git:

git init


Check files:

git status


Add all safe project files:

git add .


Check again:

git status


Make sure that these folders are NOT listed:

backend/venv/

frontend/node_modules/


Commit:

git commit -m "MediLink RBAC EHR module"


Set branch name:

git branch -M main


Connect your GitHub repository:

git remote add origin https://github.com/YOUR_USERNAME/medilink.git


Check remote:

git remote -v


Push:

git push -u origin main


============================================================
36. SHARING THE PROJECT WITH A TEAMMATE
============================================================

After pushing, send your partner the GitHub repository URL.

Example:

https://github.com/YOUR_USERNAME/medilink


If the repository is private:

Go to:

GitHub Repository

Settings

Collaborators

Add people


Add your teammate's GitHub account.


Your teammate can clone using:

git clone https://github.com/YOUR_USERNAME/medilink.git


============================================================
37. FUTURE GIT UPDATES
============================================================

Whenever you make changes:

git add .

git commit -m "Update MediLink"

git push


============================================================
38. TEAM INTEGRATION
============================================================

Other MediLink modules may include:

- Agent orchestration
- MCP
- Knowledge Graph
- AI/LLM module
- Additional workflow services


Other modules should preferably communicate with this module through FastAPI instead of reading the PostgreSQL tables directly.


Recommended integration endpoints:

GET /me

GET /doctor/patient/{patient_id}

GET /receptionist/patient/{patient_id}

POST /chat

POST /chat/upload


This keeps authentication and RBAC enforcement inside the backend.


============================================================
39. IMPORTANT NOTE ABOUT LOCALHOST
============================================================

During local development:

localhost

and:

127.0.0.1


refer to the current computer only.


For example:

localhost:5432

means PostgreSQL running on the same computer.


Similarly:

127.0.0.1:8000

means FastAPI running on the same computer.


If another team member uses a different laptop, their localhost does not point to your laptop.


For team integration, use one of:

1. Shared PostgreSQL server
2. Docker Compose
3. Separate local PostgreSQL copy on each developer machine


Docker Compose is recommended for a final reproducible team setup.


============================================================
40. CURRENT SECURITY MODEL
============================================================

Current authentication flow:

User Login

      |
      v

FastAPI checks users table

      |
      v

Password hash verification

      |
      v

JWT generated

      |
      v

JWT contains role

      |
      v

Backend checks role

      |
      +-------------------+
      |                   |
      v                   v
Doctor               Receptionist

Clinical Data        Administrative Data


Backend RBAC is authoritative.


============================================================
41. DOCTOR ACCESS FLOW
============================================================

Doctor login:

POST /login

      |
      v

JWT role = doctor

      |
      v

Doctor Dashboard

      |
      v

GET /doctor/patients

      |
      v

doctor_patient_view

      |
      v

Clinical EHR information


============================================================
42. RECEPTIONIST ACCESS FLOW
============================================================

Receptionist login:

POST /login

      |
      v

JWT role = receptionist

      |
      v

Receptionist Dashboard

      |
      v

GET /receptionist/patients

      |
      v

receptionist_patient_view

      |
      v

Administrative EHR information


============================================================
43. MEDILINK ASSISTANT FLOW
============================================================

User asks question

      |
      v

POST /chat

      |
      v

JWT identifies user role

      |
      v

Role check

      |
      +-----------------------+
      |                       |
      v                       v
Doctor                  Receptionist

Clinical                Administrative
questions               questions

      |
      v

PostgreSQL patient view

      |
      v

Response returned to frontend


============================================================
44. DOCUMENT FLOW
============================================================

User uploads PDF or TXT

      |
      v

POST /chat/upload

      |
      v

JWT authentication

      |
      v

File validation

      |
      v

PDF or TXT text extraction

      |
      v

Temporary user document context

      |
      v

User asks document question

      |
      v

POST /chat

      |
      v

Role-aware document response


============================================================
45. PROJECT DEMO SEQUENCE
============================================================

Suggested demonstration:

1. Start PostgreSQL.

2. Show patients table.

3. Show that there are 1000 synthetic patients.

4. Show users table.

5. Show doctor_patient_view.

6. Show receptionist_patient_view.

7. Start FastAPI.

8. Open Swagger.

9. Start React frontend.

10. Login as Doctor.

11. Show Doctor Dashboard.

12. Search for a patient.

13. Open Doctor Patient Detail page.

14. Show clinical fields.

15. Open MediLink AI.

16. Ask:

What is the diagnosis of MED00001?

17. Upload PDF or TXT.

18. Ask:

Summarize the uploaded document.

19. Logout.

20. Login as Receptionist.

21. Show Receptionist Dashboard.

22. Open administrative patient page.

23. Ask:

When is MED00001's next appointment?

24. Try:

What medication is MED00001 taking?

25. Show that access is denied.

26. Explain that RBAC is enforced by FastAPI and not only by the frontend.


============================================================
46. CURRENT PROJECT STATUS
============================================================

Authentication:
COMPLETE

JWT:
COMPLETE

RBAC:
COMPLETE

Doctor Dashboard:
COMPLETE

Receptionist Dashboard:
COMPLETE

Doctor Patient Detail:
COMPLETE

Receptionist Patient Detail:
COMPLETE

PostgreSQL Mock EHR:
COMPLETE

1000 Synthetic Patients:
COMPLETE

Patient Search:
COMPLETE

MediLink Assistant:
COMPLETE

PDF Upload:
COMPLETE

TXT Upload:
COMPLETE

PDF Text Extraction:
COMPLETE

Document Summary/Search:
COMPLETE

Clinical Access Blocking:
COMPLETE

Modern Frontend UI:
COMPLETE


============================================================
47. OPTIONAL FUTURE IMPROVEMENTS
============================================================

Potential future improvements:

- OCR for scanned PDFs
- Persistent document storage
- External LLM integration
- MCP integration
- Knowledge Graph integration
- Docker Compose
- Automated backend tests
- Automated frontend tests
- Audit logs
- Better token/session management
- Shared deployment
- Cloud PostgreSQL
- Production security configuration


============================================================
48. TEAM HANDOVER SUMMARY
============================================================

Backend:

FastAPI


Local backend:

http://127.0.0.1:8000


Swagger:

http://127.0.0.1:8000/docs


Database:

PostgreSQL


Database name:

medilink


Port:

5432


Authentication:

JWT Bearer Token


Roles:

doctor

receptionist


Core database objects:

patients

users

doctor_patient_view

receptionist_patient_view


Main APIs:

POST /login

GET /me

GET /doctor/patients

GET /doctor/patient/{patient_id}

GET /receptionist/patients

GET /receptionist/patient/{patient_id}

POST /chat

POST /chat/upload


Patient dataset:

1000 synthetic EHR records


Patient IDs:

MED00001 to MED01000


Document types:

PDF

TXT


Maximum document size:

5 MB


Current document storage:

Temporary FastAPI in-memory storage


============================================================
49. IMPORTANT HANDOVER NOTE
============================================================

The PostgreSQL database itself is not automatically transferred just because the source code is pushed to GitHub.

The teammate integrating this module will also need one of the following:

- SQL schema/setup script
- Synthetic patient generation script
- PostgreSQL database dump
- Dockerized PostgreSQL environment


At minimum, the integrating teammate needs:

- patients table
- users table
- doctor_patient_view
- receptionist_patient_view
- demo users
- synthetic patient records


Before final integration, it is recommended to create a database setup script or PostgreSQL dump and include it in the repository.


============================================================
50. FINAL PROJECT NOTE
============================================================

MediLink currently provides a working role-aware mock healthcare workflow.

Doctors and Receptionists interact with the same synthetic patient dataset through different authorization boundaries.

The FastAPI backend should remain the trusted access layer when this module is connected to the larger MediLink system.

Clinical data should not be exposed directly to unauthorized frontend or integration components.

All current patient information is synthetic and intended only for academic demonstration.============================================================
1. PROJECT FEATURES
============================================================

Implemented features:

- JWT-based authentication
- Role-Based Access Control (RBAC)
- Doctor login and dashboard
- Receptionist login and dashboard
- Separate clinical and administrative patient views
- PostgreSQL mock EHR
- 1000 synthetic patient records
- Patient search
- Individual patient pages
- Role-aware MediLink Assistant
- PDF upload
- TXT upload
- Text extraction from uploaded documents
- Simple document summary/search
- Clinical-information restriction for Receptionist role
- Modern healthcare dashboard frontend
- React Router navigation
- FastAPI Swagger API documentation


============================================================
2. TECHNOLOGY STACK
============================================================

Frontend:

- React
- Vite
- React Router
- Lucide React
- CSS

Backend: