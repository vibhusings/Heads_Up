import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "../index.css";
import { PhonePage } from "./PhonePage";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <PhonePage />
  </StrictMode>,
);
