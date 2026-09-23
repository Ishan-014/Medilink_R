import {
    useEffect,
    useState
  } from "react";
  
  import {
    useSearchParams
  } from "react-router-dom";
  
  import {
    Lock,
    FileText,
    BrainCircuit,
    ShieldCheck
  } from "lucide-react";
  
  import AppShell from "../components/AppShell";
  import StatusBadge from "../components/StatusBadge";
  import Chatbot from "../components/Chatbot";
  
  
  function AIWorkspace() {
    const role =
      localStorage.getItem("role");
  
  
    const [searchParams] =
      useSearchParams();
  
  
    const patientId =
      searchParams.get("patient") ||
      "MED00001";
  
  
    const [patient, setPatient] =
      useState(null);
  
  
    const [uploadedFile, setUploadedFile] =
      useState(null);
  
  
    useEffect(() => {
      const loadPatient =
        async () => {
          const token =
            localStorage.getItem(
              "access_token"
            );
  
  
          const url =
            role === "doctor"
              ? `http://127.0.0.1:8000/doctor/patient/${patientId}`
              : `http://127.0.0.1:8000/receptionist/patient/${patientId}`;
  
  
          try {
            const response =
              await fetch(
                url,
                {
                  headers: {
                    Authorization:
                      `Bearer ${token}`
                  }
                }
              );
  
  
            if (response.ok) {
              setPatient(
                await response.json()
              );
            }
  
          } catch {
            setPatient(null);
          }
        };
  
  
      loadPatient();
  
    }, [patientId, role]);
  
  
    return (
      <AppShell>
  
        <div className="ai-workspace-layout">
  
          <aside className="ai-context-column">
  
            <section className="context-card">
  
              <div className="context-card-title">
  
                <Lock size={15} />
  
                <span>
                  ACTIVE PATIENT CONTEXT
                </span>
  
                <StatusBadge tone="success">
                  LOCKED
                </StatusBadge>
  
              </div>
  
  
              <div className="context-patient">
  
                <div className="patient-avatar large">
                  {patient?.first_name?.[0] || "M"}
                  {patient?.last_name?.[0] || "P"}
                </div>
  
                <div>
                  <strong>
                    {patient
                      ? `${patient.first_name} ${patient.last_name}`
                      : patientId}
                  </strong>
  
                  <span>
                    {patientId}
                  </span>
                </div>
  
              </div>
  
  
              <div className="context-tags">
  
                {role === "doctor" ? (
                  <>
                    <span>
                      Blood:{" "}
                      {patient?.blood_group || "—"}
                    </span>
  
                    <span>
                      {patient?.department || "Clinical"}
                    </span>
                  </>
                ) : (
                  <>
                    <span>
                      {patient?.department || "Administrative"}
                    </span>
  
                    <span>
                      Appointment
                    </span>
                  </>
                )}
  
              </div>
  
            </section>
  
  
            <section className="context-card">
  
              <div className="context-section-heading">
  
                <FileText size={16} />
  
                <strong>
                  Knowledge Base Docs
                </strong>
  
              </div>
  
  
              {uploadedFile ? (
  
                <div className="knowledge-file">
  
                  <div className="file-type-box">
                    {uploadedFile.type}
                  </div>
  
                  <div>
                    <strong>
                      {uploadedFile.name}
                    </strong>
  
                    <span>
                      {uploadedFile.characters}
                      {" "}characters extracted
                    </span>
                  </div>
  
                </div>
  
              ) : (
  
                <div className="empty-doc-box">
                  Attach a PDF or TXT document in the AI composer.
                </div>
  
              )}
  
            </section>
  
  
            <section className="context-card recent-card">
  
              <span className="tiny-label">
                RECENT SESSION
              </span>
  
              <div className="recent-session active">
                <strong>
                  {patientId} AI Inquiry
                </strong>
  
                <span>
                  Current session
                </span>
              </div>
  
            </section>
  
  
            <div className="ai-sync-status">
  
              <span className="online-dot"></span>
  
              PostgreSQL EHR Connected
  
            </div>
  
          </aside>
  
  
          <section className="ai-main-panel">
  
            <div className="ai-main-header">
  
              <div className="ai-main-title">
  
                <div className="chat-logo">
                  <BrainCircuit size={21} />
                </div>
  
                <div>
                  <h1>
                    MediLink Assistant
                  </h1>
  
                  <p>
                    Role-aware EHR & document assistant
                  </p>
                </div>
  
              </div>
  
  
              <div className="ai-mode">
  
                <StatusBadge tone="success">
                  <ShieldCheck size={13} />
  
                  {role === "doctor"
                    ? "Doctor Mode"
                    : "Receptionist Mode"}
                </StatusBadge>
  
              </div>
  
            </div>
  
  
            <div className="ai-security-bar">
  
              <ShieldCheck size={14} />
  
              Context locked to{" "}
  
              <strong>
                Patient {patientId}
              </strong>
  
              <span>
                •
              </span>
  
              RBAC Enabled
  
              <span>
                •
              </span>
  
              Synthetic EHR
  
            </div>
  
  
            <Chatbot
              patientId={patientId}
              onUpload={
                setUploadedFile
              }
            />
  
          </section>
  
        </div>
  
      </AppShell>
    );
  }
  
  export default AIWorkspace;