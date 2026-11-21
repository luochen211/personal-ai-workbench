import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "@fontsource/chakra-petch/latin-600.css";
import "@fontsource/orbitron/latin-700.css";
import "./styles.css";
import "./scifi.css";
createRoot(document.getElementById("root")!).render(<React.StrictMode><App /></React.StrictMode>);
