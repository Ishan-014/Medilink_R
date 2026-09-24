
import {
  useEffect,
  useMemo,
  useState,
} from "react";

import {
  useNavigate,
} from "react-router-dom";

import {
  Users,
  CalendarCheck,
  FileClock,
  Building2,
  Search,
  Filter,
  Plus,
  Activity,
  AlertTriangle,
} from "lucide-react";

import AppShell from "../components/AppShell";
import StatCard from "../components/StatCard";
import StatusBadge from "../components/StatusBadge";


function DoctorDashboard() {
  const navigate = useNavigate();

  const [patients, setPatients] = useState([]);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [page, setPage] = useState(1);

  const rowsPerPage = 6;


  // =========================================================
  // FETCH PATIENTS FROM POSTGRESQL THROUGH FASTAPI
  // =========================================================

  const fetchPatients = async (query = "") => {
    const token = localStorage.getItem("access_token");

    if (!token) {
      navigate("/");
      return;
    }

    setLoading(true);
    setError("");

    try {
      const url = query.trim()
        ? `http://127.0.0.1:8000/doctor/patients?q=${encodeURIComponent(query)}`
        : "http://127.0.0.1:8000/doctor/patients";

      const response = await fetch(url, {
        headers: {
          Authorization: `Bearer ${token}`,
        },
      });

      // Token expired / invalid
      if (
        response.status === 401 ||
        response.status === 403
      ) {
        localStorage.clear();
        navigate("/");
        return;
      }

      if (!response.ok) {
        throw new Error("Failed to load patients");
      }

      const data = await response.json();

      // Backend returns:
      // {
      //   logged_in_as: "doctor",
      //   patients: [...]
      // }

      setPatients(data.patients || []);
      setPage(1);

    } catch (err) {
      console.error(err);
      setError("Unable to load patient records.");
    } finally {
      setLoading(false);
    }
  };


  // =========================================================
  // INITIAL LOAD
  // =========================================================

  useEffect(() => {
    const role = localStorage.getItem("role");

    if (role !== "doctor") {
      navigate("/");
      return;
    }

    fetchPatients();
  }, []);


  // =========================================================
  // PAGINATION
  // =========================================================

  const totalPages = Math.max(
    1,
    Math.ceil(patients.length / rowsPerPage)
  );

  const currentPatients = useMemo(() => {
    const start = (page - 1) * rowsPerPage;

    return patients.slice(
      start,
      start + rowsPerPage
    );
  }, [patients, page]);


  // =========================================================
  // UI
  // =========================================================

  return (
    <AppShell>

      {/* =====================================================
          HERO
      ====================================================== */}

      <section className="dashboard-hero">

        <div>

          <div className="eyebrow-row">

            <span className="eyebrow">
              CLINICAL OVERVIEW
            </span>

            <StatusBadge tone="success">
              Clinical Physician • Full Access
            </StatusBadge>

          </div>

          <h1>
            Doctor Dashboard
          </h1>

          <p>
            View active patients, electronic health records,
            clinical summaries and MediLink AI assistance.
          </p>

        </div>


        <div className="hero-actions">

          <div className="occupancy-card">

            <Activity size={22} />

            <div>

              <span>
                Synthetic EHR
              </span>

              <strong>
                Connected
              </strong>

            </div>

          </div>


          <button
            className="primary-button"
            onClick={() => navigate("/ai")}
          >
            <Plus size={18} />
            New AI Session
          </button>

        </div>

      </section>


      {/* =====================================================
          STATISTICS
      ====================================================== */}

      <section className="stats-grid">

        <StatCard
          label="PATIENTS VISIBLE"
          value={`${patients.length} Active`}
          subtitle="Available in your current EHR view"
          icon={<Users size={21} />}
          footer="Synthetic patient records"
        />


        <StatCard
          label="APPOINTMENTS TODAY"
          value="12 Slots"
          subtitle="4 completed • 8 upcoming"
          icon={<CalendarCheck size={21} />}
          footer="Demo scheduling snapshot"
        />


        <StatCard
          label="RECENT CLINICAL RECORDS"
          value={`${patients.length} Patient Records`}
          subtitle="Clinical data available"
          icon={<FileClock size={21} />}
          footer="RBAC protected"
        />


        <StatCard
          label="DEPARTMENT & UNIT"
          value="General Medicine"
          subtitle="MediLink Clinical Workspace"
          icon={<Building2 size={21} />}
          footer="PostgreSQL connected"
        />

      </section>


      {/* =====================================================
          PATIENT RECORDS
      ====================================================== */}

      <section
        className="data-card"
        id="patients"
      >

        {/* TOOLBAR */}

        <div className="patient-toolbar">

          <div className="search-box-new">

            <Search size={18} />

            <input
              value={search}
              placeholder="Search by patient ID, name, phone or condition"
              onChange={(event) => {
                setSearch(event.target.value);
              }}
              onKeyDown={(event) => {

                if (event.key === "Enter") {
                  fetchPatients(search);
                }

              }}
            />

          </div>


          <button
            className="primary-button compact-button"
            onClick={() => fetchPatients(search)}
          >
            <Filter size={17} />
            Search
          </button>


          <button
            className="secondary-button compact-button"
            onClick={() => {
              setSearch("");
              fetchPatients("");
            }}
          >
            Clear
          </button>


          <div className="table-count">
            Showing {patients.length} registered patients
          </div>

        </div>


        {/* ERROR */}

        {error && (
          <div className="error-banner">
            {error}
          </div>
        )}


        {/* ===================================================
            TABLE
        ==================================================== */}

        <div className="modern-table-wrapper">

          <table className="modern-table doctor-table">

            <thead>

              <tr>

                <th>
                  PATIENT ID
                </th>

                <th>
                  PATIENT DETAILS
                </th>

                <th>
                  BLOOD
                </th>

                <th>
                  DATE OF BIRTH
                </th>

                <th>
                  CONDITION
                </th>

                <th>
                  MEDICATION
                </th>

                <th>
                  ALLERGIES
                </th>

              </tr>

            </thead>


            <tbody>

              {/* LOADING */}

              {loading ? (

                <tr>

                  <td colSpan="7">
                    Loading patient records...
                  </td>

                </tr>

              ) : currentPatients.length === 0 ? (

                /* EMPTY */

                <tr>

                  <td colSpan="7">
                    No patient records found.
                  </td>

                </tr>

              ) : (

                /* PATIENTS */

                currentPatients.map((patient) => (

                  <tr
                    key={patient.id}
                  >

                    {/* PATIENT ID */}

                    <td>

                      <button
                        className="patient-id-pill"
                        onClick={() =>
                          navigate(
                            `/doctor/patient/${patient.id}`
                          )
                        }
                      >
                        {patient.id}
                      </button>

                    </td>


                    {/* PATIENT DETAILS */}

                    <td>

                      <div className="patient-person">

                        <div className="patient-avatar">

                          {patient.name
                            ?.split(" ")
                            .map(
                              (part) =>
                                part[0]
                            )
                            .slice(0, 2)
                            .join("")
                            .toUpperCase()
                          }

                        </div>


                        <div>

                          <strong>
                            {patient.name}
                          </strong>

                          <span>
                            {patient.gender || "Patient"}
                          </span>

                        </div>

                      </div>

                    </td>


                    {/* BLOOD GROUP */}

                    <td>

                      <StatusBadge>
                        {patient.blood_group || "—"}
                      </StatusBadge>

                    </td>


                    {/* DATE OF BIRTH */}

                    <td>

                      <strong className="table-main-text">
                        {patient.date_of_birth || "—"}
                      </strong>

                      <span className="table-sub-text">
                        Date of birth
                      </span>

                    </td>


                    {/* CONDITION */}

                    <td>

                      <strong className="table-main-text">
                        {patient.conditions || "—"}
                      </strong>

                    </td>


                    {/* MEDICATION */}

                    <td>

                      <strong className="table-main-text">
                        {patient.medications || "—"}
                      </strong>

                    </td>


                    {/* ALLERGIES */}

                    <td>

                      {patient.allergies &&
                      patient.allergies.toLowerCase() !== "none" ? (

                        <StatusBadge tone="danger">

                          <AlertTriangle size={12} />

                          {patient.allergies}

                        </StatusBadge>

                      ) : (

                        <StatusBadge>
                          None known
                        </StatusBadge>

                      )}

                    </td>

                  </tr>

                ))

              )}

            </tbody>

          </table>

        </div>


        {/* ===================================================
            PAGINATION
        ==================================================== */}

        <div className="pagination">

          <span>

            {patients.length === 0
              ? "0 records"
              : `${(page - 1) * rowsPerPage + 1}–${Math.min(
                  page * rowsPerPage,
                  patients.length
                )} of ${patients.length} records`
            }

          </span>


          <div className="pagination-controls">

            <button
              disabled={page === 1}
              onClick={() =>
                setPage(
                  Math.max(
                    1,
                    page - 1
                  )
                )
              }
            >
              Previous
            </button>


            <span className="page-current">
              {page}
            </span>


            <button
              disabled={page === totalPages}
              onClick={() =>
                setPage(
                  Math.min(
                    totalPages,
                    page + 1
                  )
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


export default DoctorDashboard;
