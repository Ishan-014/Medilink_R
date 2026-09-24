# MediLink

MediLink is a role-based healthcare operations and AI assistant prototype built for a hospital workflow demo. The project combines a React frontend, a FastAPI backend, PostgreSQL-backed EHR data, and an OpenAI + LangGraph-powered clinical assistant. The goal is to simulate a realistic clinic environment where doctors and receptionists can review patient records and ask context-aware questions through an AI interface.

This project is intended for academic, demo, and portfolio use. It uses synthetic healthcare data and is not designed for real-world clinical deployment.

## Demo overview

MediLink includes:
- a three-step staff authentication flow: credentials → ID card QR → facial recognition
- doctor and receptionist role-based dashboards
- patient search, listing, and detail views
- encrypted JWT-based session management
- AI-powered patient queries using OpenAI, LangGraph, and MCP-style tool execution
- document-aware chat with PDF/TXT upload support
- PostgreSQL-backed patient records and role-specific access control

## Why this project exists

The project demonstrates how a healthcare system can integrate:
- operational workflows for staff,
- data access rules based on roles,
- an AI assistant grounded in real patient records,
- and a practical interface for clinical ask-and-answer workflows.

It is designed as a working prototype showing how medical record access, structured EHR data, and LLM-based reasoning can be combined in a single system.

---

## Architecture

```text
React Frontend (Vite)
        │
        ▼
FastAPI Backend
        │
        ├── Auth flow: credentials -> ID card -> face -> JWT
        ├── RBAC: doctor / receptionist access checks
        ├── PostgreSQL patient queries
        ├── Document upload and chat API
        └── OpenAI + LangGraph AI orchestration
                │
                ▼
        MCP / patient tools → PostgreSQL EHR database
```

## Main features

### 1. Staff authentication
- Credential login
- QR-based ID card validation
- Face verification step
- Final JWT access token for app sessions

### 2. Role-based access control
- Doctor access for clinical data
- Receptionist access for operational data
- Protected endpoints enforced on the backend

### 3. Patient management
- Patient list and search
- Patient detail view
- Real PostgreSQL EHR data access
- Synthetic records for demo purposes

### 4. AI assistant
- Natural-language patient queries
- Tool-based routing for patient-specific questions
- PostgreSQL-grounded answers
- Context-aware chat for clinical and admin use cases

### 5. Document support
- PDF/TXT upload
- Uploaded-document summarization support in chat

---

## Tech stack

### Frontend
- React
- Vite
- React Router
- Lucide React icons

### Backend
- Python
- FastAPI
- Uvicorn
- PostgreSQL + psycopg2
- PyJWT
- pwdlib
- python-multipart
- pypdf

### AI / orchestration
- OpenAI API
- LangGraph
- MCP-style tool routing

---

## Project structure

```text
Medilink/
├── backend/
│   ├── agents/
│   │   ├── graph.py
│   │   ├── nodes.py
│   │   └── state.py
│   ├── auth/
│   │   ├── credentials.py
│   │   ├── employee_store.py
│   │   ├── facial_recognition.py
│   │   ├── id_card.py
│   │   ├── models.py
│   │   └── router.py
│   ├── mcp/
│   │   ├── patient_tools.py
│   │   └── server.py
│   ├── services/
│   │   └── patient_service.py
│   ├── main.py
│   └── __init__.py
├── frontend/
│   ├── src/
│   ├── package.json
│   ├── vite.config.js
│   └── index.html
├── scripts/
│   ├── generate_demo_card.py
│   ├── provision_staff.py
│   └── seed_users.py
├── data/
│   └── reference_faces/
├── tests/
│   └── test_mcp_patient_tools.py
├── .env
├── .gitignore
├── README.md
├── schema_probe.py
├── doctor_id_card.png
├── doctor_test_qr.png
└── requirements.txt
```

---

## Roles and access

### Doctor role
Doctors can access clinical patient information such as:
- patient conditions
- medications
- allergies
- lab summaries
- diagnosis summary
- patient overview
- doctor-facing AI assistance

### Receptionist role
Receptionists can access administrative information such as:
- patient listings
- contact information
- appointment and schedule details
- department / assignment data
- non-clinical operational context

---

## Demo accounts

The app seeds demo users for local testing.

### Doctor
- Email: doctor@medilink.demo
- Password: doctor123

### Receptionist
- Email: reception@medilink.demo
- Password: reception123

---

## Database setup

The project expects PostgreSQL to be running locally with a database named `medilink`.

Example configuration in `.env`:

```env
MEDILINK_DB_NAME=medilink
MEDILINK_DB_HOST=localhost
MEDILINK_DB_PORT=5432
MEDILINK_DB_USER=postgres
MEDILINK_DB_PASSWORD=root
```

The application expects these key tables to exist:
- `users`
- `patients`

If the database is not already populated, seed the demo records before using the app.

---

## Environment variables

Create a `.env` file in the project root with values like:

```env
MEDILINK_DB_NAME=medilink
MEDILINK_DB_HOST=localhost
MEDILINK_DB_PORT=5432
MEDILINK_DB_USER=postgres
MEDILINK_DB_PASSWORD=root

MEDILINK_JWT_SECRET=change-this-to-a-random-secret
MEDILINK_CARD_SECRET=change-this-to-a-random-card-secret

OPENAI_API_KEY=your_openai_api_key
OPENAI_MODEL=gpt-5-mini
```

> Important: never commit your real `.env` values. The included `.gitignore` protects them.

---

## Backend setup

From the project root:

```bash
cd C:\Users\Ishan K\Medilink
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

If `requirements.txt` is missing, install the main dependencies manually:

```bash
.\.venv\Scripts\python.exe -m pip install fastapi uvicorn psycopg2-binary python-dotenv pyjwt pwdlib pypdf python-multipart qrcode deepface openai langgraph
```

Start the API server:

```bash
cd C:\Users\Ishan K\Medilink
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

Swagger docs:

```text
http://127.0.0.1:8000/docs
```

---

## Frontend setup

From the frontend folder:

```bash
cd C:\Users\Ishan K\Medilink\frontend
npm install
npm run dev
```

The frontend runs at:

```text
http://localhost:5173
```

---

## Core endpoints

### Authentication
- `POST /auth/login/credentials`
- `POST /auth/login/id-card`
- `POST /auth/login/face`
- `GET /me`

### Doctor endpoints
- `GET /doctor/patients`
- `GET /doctor/patient/{patient_id}`

### Receptionist endpoints
- `GET /receptionist/patients`
- `GET /receptionist/patient/{patient_id}`

### AI chat endpoints
- `POST /chat`
- `POST /chat/upload`

---

## AI assistant behavior

The chatbot is not a generic response bot. It is built to reason over real EHR data and only call tools when the prompt is relevant to patient information.

Examples of valid AI queries:

```text
Find information on Aarav.
What is the diagnosis of PAT-001?
Show me allergies for Aarav Sharma.
Summarize this patient record.
```

The model uses patient tools grounded in PostgreSQL records. Plain greetings such as “hello there” remain conversational; patient-specific questions trigger real lookup behavior.

---

## Testing

Run the project regression tests:

```bash
cd C:\Users\Ishan K\Medilink
.\.venv\Scripts\python.exe -m pytest tests/test_mcp_patient_tools.py -q
```

---

## Important notes

- This is a demo project using synthetic patient records.
- The login flow uses a QR code and facial recognition as a prototype authentication chain.
- The AI assistant relies on OpenAI and PostgreSQL-backed patient data.
- The app is built for demonstration and learning, not clinical deployment.

---

## License

This project is intended for educational and demonstration use.

## Credits

MediLink combines:
- FastAPI backend
- React + Vite frontend
- PostgreSQL EHR data
- OpenAI LLM integration
- LangGraph orchestration
- Multi-layer staff authentication
