import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { SessionProvider, useSession } from "./context/SessionContext";
import Login from "./pages/Login";
import Chat from "./pages/Chat";
import OAuthComplete from "./pages/OAuthComplete";

function RootRedirect() {
  const { isLoggedIn } = useSession();
  return <Navigate to={isLoggedIn ? "/chat" : "/login"} replace />;
}

export default function App() {
  return (
    <SessionProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<RootRedirect />} />
          <Route path="/login" element={<Login />} />
          <Route path="/oauth-complete" element={<OAuthComplete />} />
          <Route path="/chat" element={<Chat />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </SessionProvider>
  );
}
