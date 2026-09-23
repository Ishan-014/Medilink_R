import {
    useEffect,
    useMemo,
    useState
  } from "react";
  
  import {
    useNavigate
  } from "react-router-dom";
  
  import {
    ClipboardCheck,
    CalendarDays,
    UserPlus,
    Armchair,
    Search,
    Plus,
    Users
  } from "lucide-react";
  
  import AppShell from "../components/AppShell";
  import StatCard from "../components/StatCard";
  import StatusBadge from "../components/StatusBadge";
  
  
  function ReceptionistDashboard() {
    const navigate =
      useNavigate();
  
  
    const [patients, setPatients] =
      useState([]);
  
    const [search, setSearch] =
      useState("");
  
    const [loading, setLoading] =
      useState(true);
  
    const [error, setError] =
      useState("");
  
    const [page, setPage] =
      useState(1);
  
  
    const rowsPerPage = 6;
  
  
    const fetchPatients =
      async (query = "") => {
        const token =
          localStorage.getItem(
            "access_token"
          );
  
  
        setLoading(true);
        setError("");
  
  
        try {
          const url =
            query.trim()
              ? `http://127.0.0.1:8000/receptionist/patients?q=${encodeURIComponent(query)}`
              : "http://127.0.0.1:8000/receptionist/patients";
  
  
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
  
  
          if (
            response.status === 401 ||
            response.status === 403
          ) {
            localStorage.clear();
            navigate("/");
            return;
          }
  
  
          const data =
            await response.json();
  
  
          setPatients(
            data.patients || []
          );
  
          setPage(1);
  
        } catch {
          setError(
            "Unable to load patient records."
          );
        } finally {
          setLoading(false);
        }
      };
  
  
    useEffect(() => {
      if (
        localStorage.getItem("role")
        !== "receptionist"
      ) {
        navigate("/");
        return;
      }
  
      fetchPatients();
    }, []);
  
  
    const totalPages =
      Math.max(
        1,
        Math.ceil(
          patients.length /
          rowsPerPage
        )
      );
  
  
    const visiblePatients =
      useMemo(() => {
        const start =
          (page - 1) *
          rowsPerPage;
  
        return patients.slice(
          start,
          start + rowsPerPage
        );
      }, [patients, page]);
  
  
    return (
      <AppShell>
  
        <section className="dashboard-hero">
  
          <div>
  
            <div className="eyebrow-row">
  
              <StatusBadge tone="info">
                ROLE: RECEPTIONIST • ADMINISTRATIVE ACCESS
              </StatusBadge>
  
              <StatusBadge>
                Desk Terminal
              </StatusBadge>
  
            </div>
  
  
            <h1>
              Receptionist Dashboard
            </h1>
  
  
            <p>
              Manage patient registration, appointments,
              insurance details and administrative enquiries.
            </p>
  
          </div>
  
  
          <div className="hero-actions">
  
            <button className="secondary-button">
              <Users size={18} />
              Quick Patient Intake
            </button>
  
            <button className="primary-button">
              <Plus size={18} />
              New Appointment
            </button>
  
          </div>
  
        </section>
  
  
        <section className="stats-grid">
  
          <StatCard
            label="TODAY'S INTAKE"
            value="32"
            subtitle="18 completed • 14 queued"
            icon={<ClipboardCheck size={21} />}
            footer="Synthetic scheduling"
          />
  
          <StatCard
            label="SCHEDULED APPOINTMENTS"
            value="45"
            subtitle="Across demo departments"
            icon={<CalendarDays size={21} />}
            footer="Administrative view"
          />
  
          <StatCard
            label="NEW REGISTRATIONS"
            value="8"
            subtitle="Insurance profiles verified"
            icon={<UserPlus size={21} />}
            footer="Demo snapshot"
          />
  
          <StatCard
            label="WAITING ROOM"
            value="4"
            subtitle="Average wait: 11 mins"
            icon={<Armchair size={21} />}
            footer="Status: Smooth"
          />
  
        </section>
  
  
        <section
          className="data-card"
          id="patients"
        >
  
          <div className="patient-toolbar">
  
            <div className="search-box-new">
  
              <Search size={18} />
  
              <input
                value={search}
                placeholder="Search by patient ID, patient name, doctor or department"
                onChange={(event) =>
                  setSearch(
                    event.target.value
                  )
                }
                onKeyDown={(event) => {
                  if (
                    event.key === "Enter"
                  ) {
                    fetchPatients(
                      search
                    );
                  }
                }}
              />
  
            </div>
  
  
            <button
              className="primary-button compact-button"
              onClick={() =>
                fetchPatients(
                  search
                )
              }
            >
              <Search size={17} />
              Search
            </button>
  
  
            <button
              className="secondary-button compact-button"
              onClick={() => {
                setSearch("");
                fetchPatients("");
              }}
            >
              Reset Filter
            </button>
  
          </div>
  
  
          {error && (
            <div className="error-banner">
              {error}
            </div>
          )}
  
  
          <div className="modern-table-wrapper">
  
            <table className="modern-table">
  
              <thead>
                <tr>
                  <th>PATIENT ID</th>
                  <th>PATIENT NAME</th>
                  <th>PHONE</th>
                  <th>INSURANCE PROVIDER</th>
                  <th>ASSIGNED DOCTOR</th>
                  <th>DEPARTMENT</th>
                  <th>NEXT APPOINTMENT</th>
                  <th>STATUS</th>
                </tr>
              </thead>
  
  
              <tbody>
  
                {loading ? (
  
                  <tr>
                    <td colSpan="8">
                      Loading patient records...
                    </td>
                  </tr>
  
                ) : visiblePatients.length === 0 ? (
  
                  <tr>
                    <td colSpan="8">
                      No patient records found.
                    </td>
                  </tr>
  
                ) : (
  
                  visiblePatients.map(
                    (patient) => (
  
                      <tr
                        key={
                          patient.patient_id
                        }
                      >
  
                        <td>
                          <button
                            className="patient-id-pill"
                            onClick={() =>
                              navigate(
                                `/receptionist/patient/${patient.patient_id}`
                              )
                            }
                          >
                            {patient.patient_id}
                          </button>
                        </td>
  
  
                        <td>
                          <div className="patient-person">
  
                            <div className="patient-avatar">
                              {patient.first_name?.[0]}
                              {patient.last_name?.[0]}
                            </div>
  
                            <strong>
                              {patient.first_name}{" "}
                              {patient.last_name}
                            </strong>
  
                          </div>
                        </td>
  
  
                        <td>
                          {patient.phone || "—"}
                        </td>
  
  
                        <td>
                          {patient.insurance_provider || "—"}
                        </td>
  
  
                        <td>
                          <StatusBadge tone="success">
                            {patient.assigned_doctor || "—"}
                          </StatusBadge>
                        </td>
  
  
                        <td>
                          {patient.department || "—"}
                        </td>
  
  
                        <td>
                          {patient.next_appointment || "—"}
                        </td>
  
  
                        <td>
                          <StatusBadge
                            tone={
                              patient.appointment_status
                                ?.toLowerCase()
                                .includes("cancel")
                                ? "danger"
                                : "success"
                            }
                          >
                            {patient.appointment_status || "Scheduled"}
                          </StatusBadge>
                        </td>
  
                      </tr>
  
                    )
                  )
  
                )}
  
              </tbody>
  
            </table>
  
          </div>
  
  
          <div className="pagination">
  
            <span>
              {patients.length === 0
                ? "0 entries"
                : `Showing ${(page - 1) * rowsPerPage + 1} to ${Math.min(
                    page * rowsPerPage,
                    patients.length
                  )} of ${patients.length} entries`}
            </span>
  
  
            <div className="pagination-controls">
  
              <button
                disabled={page === 1}
                onClick={() =>
                  setPage(
                    page - 1
                  )
                }
              >
                Previous
              </button>
  
              <span className="page-current">
                {page}
              </span>
  
              <button
                disabled={
                  page === totalPages
                }
                onClick={() =>
                  setPage(
                    page + 1
                  )
                }
              >
                Next
              </button>
  
            </div>
  
          </div>
  
        </section>
  
      </AppShell>
    );
  }
  
  export default ReceptionistDashboard;