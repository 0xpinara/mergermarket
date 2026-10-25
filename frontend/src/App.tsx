import { Link, Route, Routes } from "react-router-dom";
import { Shell } from "./components/Shell";
import { Empty } from "./components/ui";
import { CommandCenter } from "./pages/CommandCenter";
import { Workspace } from "./pages/Workspace";
import { Findings } from "./pages/Findings";
import { Proof } from "./pages/Proof";
import { Intel } from "./pages/Intel";
import { About } from "./pages/About";

function NotFound() {
  return (
    <div className="page">
      <Empty art={" ┌───────┐\n │  404  │\n └───┬───┘\n     ∅"} title="Nothing at this address"
             actions={<Link to="/" className="btn primary">Back to Command Center</Link>}>
        The page you were looking for isn’t part of this audit.
      </Empty>
    </div>
  );
}

export function App() {
  return (
    <Shell>
      <Routes>
        <Route path="/" element={<CommandCenter />} />
        <Route path="/deals/:id/workspace" element={<Workspace />} />
        <Route path="/deals/:id/findings" element={<Findings />} />
        <Route path="/deals/:id/proof" element={<Proof />} />
        <Route path="/intel" element={<Intel />} />
        <Route path="/about" element={<About />} />
        <Route path="*" element={<NotFound />} />
      </Routes>
    </Shell>
  );
}
