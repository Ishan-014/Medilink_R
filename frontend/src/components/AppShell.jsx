import Sidebar from "./Sidebar";
import Topbar from "./Topbar";

function AppShell({ children }) {
  return (
    <div className="app-shell">
      <Sidebar />

      <div className="app-shell-right">
        <Topbar />

        <main className="workspace">
          {children}
        </main>
      </div>
    </div>
  );
}

export default AppShell;