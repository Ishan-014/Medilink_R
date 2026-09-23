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
    UserRound,
    CreditCard,
    Building2,
    ShieldCheck,
    Phone,
    Mail,
    MapPin,
    CalendarDays
  } from "lucide-react";
  
  import AppShell from "../components/AppShell";
  import StatusBadge from "../components/StatusBadge";
  
  
  function ReceptionistPatient() {
    const navigate =
      useNavigate();
  
    const { patientId } =
      useParams();
  
  
    const [patient, setPatient] =
      useState(null);
  
  
    useEffect(() => {
      const load =
        async () => {
          const token =
            localStorage.getItem(
              "access_token"
            );
  
  
          const response =
            await fetch(
              `http://127.0.0.1:8000/receptionist/patient/${patientId}`,
              {
                headers: {
                  Authorization:
                    `Bearer ${token}`
                }
              }
            );
  
  
          if (!response.ok) {
            navigate(
              "/receptionist"
            );
  
            return;
          }
  
  
          setPatient(
            await response.json()
          );
        };
  
  
      load();
    }, [patientId]);
  
  
    if (!patient) {
      return (
        <AppShell>
          <div className="loading-card">
            Loading patient record...
          </div>
        </AppShell>
      );
    }
  
  
    return (
      <AppShell>
  
        <button
          className="back-button"
          onClick={() =>
            navigate(
              "/receptionist"
            )
          }
        >
          <ArrowLeft size={18} />
          Back to Patient Directory
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
  
                <StatusBadge>
                  MRN: {patient.patient_id}
                </StatusBadge>
  
              </div>
  
  
              <div className="patient-meta">
  
                <span>
                  {patient.date_of_birth || "DOB unavailable"}
                </span>
  
                <span>•</span>
  
                <span>
                  {patient.gender || "—"}
                </span>
  
                <span>•</span>
  
                <StatusBadge tone="success">
                  Active Account
                </StatusBadge>
  
              </div>
  
            </div>
  
          </div>
  
  
          <button className="primary-button">
            <ShieldCheck size={18} />
            Verify Insurance
          </button>
  
        </section>
  
  
        <div className="admin-patient-grid">
  
          <section className="clinical-card">
  
            <div className="section-heading">
  
              <div className="section-icon">
                <UserRound size={18} />
              </div>
  
              <div>
                <h2>
                  Demographics & Contact Information
                </h2>
  
                <p>
                  Administrative patient record
                </p>
              </div>
  
            </div>
  
  
            <div className="info-grid">
  
              <div className="info-item">
                <span>FULL LEGAL NAME</span>
  
                <strong>
                  {patient.first_name}{" "}
                  {patient.last_name}
                </strong>
              </div>
  
  
              <div className="info-item">
                <span>PRIMARY PHONE</span>
  
                <strong>
                  <Phone size={14} />
                  {patient.phone || "—"}
                </strong>
              </div>
  
  
              <div className="info-item">
                <span>EMAIL ADDRESS</span>
  
                <strong>
                  <Mail size={14} />
                  {patient.email || "—"}
                </strong>
              </div>
  
  
              <div className="info-item">
                <span>EMERGENCY CONTACT</span>
  
                <strong>
                  {patient.emergency_contact || "—"}
                </strong>
              </div>
  
            </div>
  
  
            <div className="wide-info-box">
  
              <MapPin size={17} />
  
              <div>
                <span>
                  RESIDENTIAL ADDRESS
                </span>
  
                <strong>
                  {patient.address || "—"}
                </strong>
              </div>
  
            </div>
  
          </section>
  
  
          <section className="clinical-card">
  
            <div className="section-heading">
  
              <div className="section-icon">
                <CreditCard size={18} />
              </div>
  
              <div>
                <h2>
                  Insurance & Billing
                </h2>
  
                <p>
                  Administrative eligibility record
                </p>
              </div>
  
            </div>
  
  
            <div className="insurance-card">
  
              <span>
                INSURANCE PROVIDER
              </span>
  
              <h3>
                {patient.insurance_provider || "—"}
              </h3>
  
  
              <div className="insurance-policy">
  
                <div>
                  <span>
                    POLICY / MEMBER ID
                  </span>
  
                  <strong>
                    {patient.policy_number || "—"}
                  </strong>
                </div>
  
                <StatusBadge tone="success">
                  In Network
                </StatusBadge>
  
              </div>
  
            </div>
  
          </section>
  
        </div>
  
  
        <section className="clinical-card">
  
          <div className="section-heading">
  
            <div className="section-icon">
              <Building2 size={18} />
            </div>
  
            <div>
              <h2>
                Hospital Administration & Scheduling
              </h2>
  
              <p>
                Provider assignment and upcoming appointments
              </p>
            </div>
  
          </div>
  
  
          <div className="schedule-grid">
  
            <div className="schedule-box">
              <span>
                ASSIGNED ATTENDING
              </span>
  
              <strong>
                {patient.assigned_doctor || "—"}
              </strong>
            </div>
  
  
            <div className="schedule-box">
              <span>
                DEPARTMENT & UNIT
              </span>
  
              <strong>
                {patient.department || "—"}
              </strong>
            </div>
  
  
            <div className="schedule-box">
              <span>
                UPCOMING EVENT
              </span>
  
              <strong>
                <CalendarDays size={15} />
                {patient.next_appointment || "—"}
              </strong>
  
              <StatusBadge tone="success">
                {patient.appointment_status || "Scheduled"}
              </StatusBadge>
            </div>
  
          </div>
  
        </section>
  
  
        <section className="rbac-notice">
  
          <ShieldCheck size={28} />
  
          <div>
            <h3>
              Role-Based Access Policy
              (Receptionist Level)
            </h3>
  
            <p>
              Administrative users have access to demographic,
              contact, insurance and appointment information.
              Diagnosis, medication, allergy and laboratory
              information remain restricted to authorized
              clinical users.
            </p>
          </div>
  
          <StatusBadge tone="info">
            Tier 1 • Administrative
          </StatusBadge>
  
        </section>
  
      </AppShell>
    );
  }
  
  export default ReceptionistPatient;