import {
    useEffect,
    useState
  } from "react";
  
  import {
    useNavigate,
    useParams
  } from "react-router-dom";
  
  import {
    ArrowLeft,
    Printer,
    Pill,
    AlertTriangle,
    FlaskConical,
    Stethoscope,
    Activity,
    ShieldAlert
  } from "lucide-react";
  
  import AppShell from "../components/AppShell";
  import StatusBadge from "../components/StatusBadge";
  import Chatbot from "../components/Chatbot";
  
  
  function DoctorPatient() {
    const navigate =
      useNavigate();
  
    const { patientId } =
      useParams();
  
  
    const [patient, setPatient] =
      useState(null);
  
    const [loading, setLoading] =
      useState(true);
  
  
    useEffect(() => {
      const loadPatient =
        async () => {
          const token =
            localStorage.getItem(
              "access_token"
            );
  
  
          try {
            const response =
              await fetch(
                `http://127.0.0.1:8000/doctor/patient/${patientId}`,
                {
                  headers: {
                    Authorization:
                      `Bearer ${token}`
                  }
                }
              );
  
  
            if (!response.ok) {
              navigate("/doctor");
              return;
            }
  
  
            const data =
              await response.json();
  
  
            setPatient(data);
  
          } finally {
            setLoading(false);
          }
        };
  
  
      loadPatient();
    }, [patientId]);
  
  
    if (loading) {
      return (
        <AppShell>
          <div className="loading-card">
            Loading patient record...
          </div>
        </AppShell>
      );
    }
  
  
    if (!patient) {
      return null;
    }
  
  
    const medications =
      patient.medications
        ? patient.medications
            .split(/[,;]/)
            .map((item) =>
              item.trim()
            )
            .filter(Boolean)
        : [];
  
  
    return (
      <AppShell>
  
        <button
          className="back-button"
          onClick={() =>
            navigate("/doctor")
          }
        >
          <ArrowLeft size={18} />
          Back to Patients
        </button>
  
  
        <section className="patient-header-card">
  
          <div className="patient-header-main">
  
            <div className="large-avatar">
              {patient.first_name?.[0]}
              {patient.last_name?.[0]}
            </div>
  
  
            <div>
  
              <div className="patient-title-row">
  
                <h1>
                  {patient.first_name}{" "}
                  {patient.last_name}
                </h1>
  
                <StatusBadge tone="success">
                  {patient.patient_id}
                </StatusBadge>
  
              </div>
  
  
              <div className="patient-meta">
  
                <span>
                  {patient.gender || "—"}
                </span>
  
                <span>•</span>
  
                <span>
                  Blood:{" "}
                  <strong>
                    {patient.blood_group || "—"}
                  </strong>
                </span>
  
                <span>•</span>
  
                <span className="teal-text">
                  {patient.department || "—"}
                </span>
  
              </div>
  
            </div>
  
          </div>
  
  
          <div className="patient-header-actions">
  
            <button className="secondary-button">
              <Stethoscope size={17} />
              Clinical Record
            </button>
  
            <button className="primary-button">
              <Printer size={17} />
              Print Summary
            </button>
  
          </div>
  
        </section>
  
  
        <div className="patient-clinical-layout">
  
          <div className="patient-clinical-main">
  
            <section className="clinical-card">
  
              <div className="section-heading">
  
                <div className="section-icon">
                  <Stethoscope size={18} />
                </div>
  
                <div>
                  <h2>
                    Primary & Secondary Diagnosis
                  </h2>
  
                  <p>
                    Latest recorded clinical diagnosis
                  </p>
                </div>
  
              </div>
  
  
              <div className="diagnosis-grid">
  
                <div className="diagnosis-box">
  
                  <span className="tiny-label teal-text">
                    PRIMARY ACTIVE
                  </span>
  
                  <h3>
                    {patient.diagnosis_summary ||
                      "No diagnosis recorded"}
                  </h3>
  
                  <p>
                    Clinical information retrieved from the
                    synthetic MediLink EHR.
                  </p>
  
                  <StatusBadge tone="success">
                    Record available
                  </StatusBadge>
  
                </div>
  
  
                <div className="diagnosis-box">
  
                  <span className="tiny-label">
                    PATIENT PROFILE
                  </span>
  
                  <h3>
                    {patient.department || "General Medicine"}
                  </h3>
  
                  <p>
                    Assigned care provider:
                  </p>
  
                  <strong>
                    {patient.assigned_doctor || "—"}
                  </strong>
  
                </div>
  
              </div>
  
            </section>
  
  
            <section className="clinical-card danger-card">
  
              <div className="section-heading">
  
                <div className="section-icon danger">
                  <AlertTriangle size={18} />
                </div>
  
                <div>
                  <h2>
                    Critical Allergies & Contraindications
                  </h2>
                </div>
  
              </div>
  
  
              <div className="allergy-panel">
  
                <ShieldAlert size={22} />
  
                <div>
                  <strong>
                    {patient.allergies ||
                      "No known allergies recorded"}
                  </strong>
  
                  <p>
                    Allergy information is visible only to
                    authorized clinical users.
                  </p>
                </div>
  
              </div>
  
            </section>
  
  
            <section className="clinical-card">
  
              <div className="section-heading">
  
                <div className="section-icon">
                  <Pill size={18} />
                </div>
  
                <div>
                  <h2>
                    Active Medication Regimen
                  </h2>
  
                  <p>
                    Medication information from MediLink records
                  </p>
                </div>
  
              </div>
  
  
              <div className="medication-list">
  
                {medications.length > 0
                  ? medications.map(
                      (medication, index) => (
  
                        <div
                          className="medication-row"
                          key={index}
                        >
  
                          <div>
                            <strong>
                              {medication}
                            </strong>
  
                            <span>
                              Active medication
                            </span>
                          </div>
  
                          <StatusBadge tone="success">
                            Active
                          </StatusBadge>
  
                        </div>
  
                      )
                    )
                  : (
                    <div className="empty-state">
                      No medication listed.
                    </div>
                  )}
  
              </div>
  
            </section>
  
  
            <section className="clinical-card">
  
              <div className="section-heading">
  
                <div className="section-icon">
                  <FlaskConical size={18} />
                </div>
  
                <div>
                  <h2>
                    Latest Laboratory & Diagnostic Summary
                  </h2>
  
                  <p>
                    Most recent available laboratory summary
                  </p>
                </div>
  
              </div>
  
  
              <div className="lab-summary-card">
  
                <Activity size={25} />
  
                <div>
                  <span className="tiny-label">
                    LAB SUMMARY
                  </span>
  
                  <p>
                    {patient.lab_summary ||
                      "No laboratory summary recorded."}
                  </p>
                </div>
  
              </div>
  
            </section>
  
          </div>
  
  
          <aside className="patient-ai-column">
  
            <Chatbot
              compact
              patientId={
                patient.patient_id
              }
            />
  
            <button
              className="full-ai-button"
              onClick={() =>
                navigate(
                  `/ai?patient=${patient.patient_id}`
                )
              }
            >
              Open Full AI Workspace →
            </button>
  
          </aside>
  
        </div>
  
      </AppShell>
    );
  }
  
  export default DoctorPatient;