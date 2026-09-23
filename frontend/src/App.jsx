import {
  BrowserRouter,
  Routes,
  Route,
  Navigate
} from "react-router-dom";

import Login from "./pages/Login";
import DoctorDashboard from "./pages/DoctorDashboard";
import ReceptionistDashboard from "./pages/ReceptionistDashboard";
import DoctorPatient from "./pages/DoctorPatient";
import ReceptionistPatient from "./pages/ReceptionistPatient";
import AIWorkspace from "./pages/AIWorkspace";


function App() {
  return (
    <BrowserRouter>

      <Routes>

        <Route
          path="/"
          element={<Login />}
        />


        <Route
          path="/doctor"
          element={<DoctorDashboard />}
        />


        <Route
          path="/doctor/patient/:patientId"
          element={<DoctorPatient />}
        />


        <Route
          path="/receptionist"
          element={<ReceptionistDashboard />}
        />


        <Route
          path="/receptionist/patient/:patientId"
          element={<ReceptionistPatient />}
        />


        <Route
          path="/ai"
          element={<AIWorkspace />}
        />


        <Route
          path="*"
          element={
            <Navigate
              to="/"
              replace
            />
          }
        />

      </Routes>

    </BrowserRouter>
  );
}

export default App;