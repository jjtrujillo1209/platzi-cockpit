import { useEffect, useRef, useState } from "react";
import { api, getToken, setToken } from "../lib/api";
import { Zap, ShieldCheck, AlertTriangle } from "lucide-react";

declare global {
  interface Window { google?: any; }
}

type State = "loading" | "open" | "login" | "authed";

export function AuthGate({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<State>("loading");
  const [cfg, setCfg] = useState<{ enabled: boolean; client_id: string; allowed_domain: string } | null>(null);
  const [user, setUser] = useState<{ email: string; name: string } | null>(null);
  const [err, setErr] = useState("");
  const btnRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.authConfig().then((c) => {
      setCfg(c);
      if (!c.enabled) { setState("open"); return; }
      if (getToken()) { setState("authed"); return; }  // se valida en el 1er request
      setState("login");
    }).catch(() => setState("open"));
  }, []);

  useEffect(() => {
    if (state !== "login" || !cfg?.client_id) return;
    const init = () => {
      window.google?.accounts.id.initialize({
        client_id: cfg.client_id,
        callback: async (resp: any) => {
          setErr("");
          try {
            const r = await api.googleLogin(resp.credential);
            if (r.ok && r.token) {
              setToken(r.token);
              setUser({ email: r.email, name: r.name });
              setState("authed");
            } else {
              setErr(r.error || "No autorizado");
            }
          } catch (e: any) {
            setErr("Solo se permite el dominio @" + cfg.allowed_domain);
          }
        },
      });
      if (btnRef.current) {
        window.google?.accounts.id.renderButton(btnRef.current, {
          theme: "filled_black", size: "large", shape: "pill", text: "signin_with",
        });
      }
    };
    if (window.google?.accounts?.id) { init(); return; }
    const s = document.createElement("script");
    s.src = "https://accounts.google.com/gsi/client";
    s.async = true; s.onload = init;
    document.body.appendChild(s);
  }, [state, cfg]);

  if (state === "loading")
    return <div className="grid h-screen place-items-center text-white/40">Cargando…</div>;

  if (state === "login")
    return (
      <div className="grid h-screen place-items-center px-6">
        <div className="card w-full max-w-md p-8 text-center">
          <div className="mx-auto mb-5 grid h-14 w-14 place-items-center rounded-2xl bg-platzi shadow-glow">
            <Zap size={26} className="text-ink-950" fill="#13161c" />
          </div>
          <h1 className="text-xl font-extrabold">Platzi Cockpit</h1>
          <p className="mt-1 text-sm text-white/45">Outreach OS · acceso interno</p>
          <div className="my-6 h-px bg-white/10" />
          <p className="mb-4 flex items-center justify-center gap-1.5 text-sm text-white/60">
            <ShieldCheck size={15} className="text-volt" />
            Ingresa con tu cuenta <b className="text-white">@{cfg?.allowed_domain}</b>
          </p>
          <div ref={btnRef} className="flex justify-center" />
          {err && (
            <div className="mt-4 flex items-center justify-center gap-2 rounded-xl border border-platzi/30 bg-platzi/[0.06] px-3 py-2 text-xs text-platzi">
              <AlertTriangle size={14} /> {err}
            </div>
          )}
        </div>
      </div>
    );

  return <>{typeof children === "function" ? (children as any)({ user, cfg }) : children}</>;
}

export function logout() {
  setToken("");
  location.reload();
}
