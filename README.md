# MediLink

MediLink is a healthcare workflow prototype built for a doctor/receptionist environment with a role-aware AI assistant, PostgreSQL-backed patient records, and a FastAPI backend. The system combines patient data access, authentication, RBAC, and an OpenAI + LangGraph powered clinical assistant.

This project is designed as a demo / academic prototype and uses synthetic healthcare data. It is not meant for production clinical use.

## Key features

- Multi-step staff login flow: credentials → ID card QR → face verification
- JWT-based authentication and role checks for doctor and receptionist
- PostgreSQL patient database and role-specific data access
- Doctor and receptionist dashboards with patient listing and details
- AI assistant for patient queries using OpenAI + LangGraph + MCP-style tool routing
- Real EHR tool execution against PostgreSQL patient records
- PDF/TXT document upload and document-aware chat support
- Frontend built with React + Vite

## Tech stack

### Frontend
- React
- Vite
- React Router
- Lucide icons

### Backend
- Python
- FastAPI
- Uvicorn
- PostgreSQL (psycopg2)
- PyJWT
- pwdlib
- python-multipart
- pypdf

### AI / Agent layer
- OpenAI API
- LangGraph
- MCP-style patient tools

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
│   ├── main.py
│   ├── mcp/
│   │   └── patient_tools.py
│   ├── services/
│   │   └── patient_service.py
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
└── schema_probe.py
```

## Architecture overview

```text
Browser / React UI
        │
        ▼
FastAPI backend
        │
        ├── Auth pipeline: credentials -> QR -> face -> JWT
        ├── RBAC checks for doctor/receptionist access
        ├── PostgreSQL patient queries
        └── OpenAI + LangGraph agent
                │
                ▼
         Patient tools / EHR data
```

## Roles

### Doctor
Doctor users can access clinical patient records and discussion-based patient queries such as:

- patient conditions
- medications
- allergies
- lab summaries
- clinical brief generation
- patient summaries

### Receptionist
Receptionist users can access administrative patient information such as:

- appointment data
- contact details
- department info
- assigned doctor
- patient list/search

## Demo login accounts

The app seeds demo staff accounts for local use.

### Doctor
- Email: doctor@medilink.demo
- Password: doctor123

### Receptionist
- Email: reception@medilink.demo
- Password: reception123

## Database setup

The project expects a PostgreSQL database named `medilink`.

Create the database and ensure your local PostgreSQL connection details match the values in `.env`:

```env
MEDILINK_DB_NAME=medilink
MEDILINK_DB_HOST=localhost
MEDILINK_DB_PORT=5432
MEDILINK_DB_USER=postgres
MEDILINK_DB_PASSWORD=root
```

The app uses the following key tables/records:

- `users` — staff identities, roles, password hashes
- `patients` — synthetic patient records

If the database is empty, seed the app users and patient data before running the app.

## Environment variables

Create a `.env` file in the project root with values similar to:

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

> Keep secrets out of source control. The included `.gitignore` ignores `.env` files.

## Backend setup

From the project root:

```bash
cd C:\Users\Ishan K\Medilink
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

If the project does not yet include a `requirements.txt`, install the required Python packages manually:

```bash
.\.venv\Scripts\python.exe -m pip install fastapi uvicorn psycopg2-binary python-dotenv pyjwt pwdlib pypdf python-multipart qrcode deepface openai langgraph
```

Then start the backend:

```bash
cd C:\Users\Ishan K\Medilink
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

The API docs are available at:

```text
http://127.0.0.1:8000/docs
```

## Frontend setup

From the frontend folder:

```bash
cd C:\Users\Ishan K\Medilink\frontend
npm install
npm run dev
```

The app should run at:

```text
http://localhost:5173
```

## API highlights

### Authentication
- `POST /auth/login/credentials`
- `POST /auth/login/id-card`
- `POST /auth/login/face`
- `GET /me`

### Doctor routes
- `GET /doctor/patients`
- `GET /doctor/patient/{patient_id}`

### Receptionist routes
- `GET /receptionist/patients`
- `GET /receptionist/patient/{patient_id}`

### AI chat
- `POST /chat`
- `POST /chat/upload`

## AI assistant behavior

The AI assistant uses a LangGraph-based workflow with tool routing for patient-related queries. It only calls patient tools when the request is clearly about a medical record or patient-specific information. General greetings or non-patient conversation remain conversational instead of forcing a tool call.

Examples of supported queries:

```text
What is the diagnosis of PAT-001?
Show me allergies for Aarav Sharma.
Find information on Aarav.
Summarize the patient record.
```

## Running tests

The project includes patient-tool and routing regression tests.

```bash
cd C:\Users\Ishan K\Medilink
.\.venv\Scripts\python.exe -m pytest tests/test_mcp_patient_tools.py -q
```

## Notes

- The system uses synthetic demo data only.
- The login flow includes a QR-based ID card verification stage and face recognition step.
- The AI assistant uses PostgreSQL as the source of truth for patient information.
- Access to clinical and administrative routes is enforced by backend RBAC checks.

## License

This project is intended for educational and demonstration use.

## Credits

This project is a healthcare AI prototype combining:
- FastAPI backend
- React frontend
- PostgreSQL EHR data
- OpenAI LLM
- LangGraph orchestration
- Multi-step staff authentication
