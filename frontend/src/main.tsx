import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import { AuthGate } from "./components/AuthGate";
import { BulkJobProvider } from "./state/bulkJobStore";
import "./index.css";

const qc = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false, staleTime: 5000 } },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={qc}>
      <AuthGate>
        <BulkJobProvider>
          <App />
        </BulkJobProvider>
      </AuthGate>
    </QueryClientProvider>
  </React.StrictMode>
);
