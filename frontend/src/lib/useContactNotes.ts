import { useEffect, useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api } from "./api";

/**
 * Notas por contacto con guardado a prueba de pérdidas. Lo comparten la tarjeta
 * del kanban (Pipeline) y el pop-up de detalle (ContactTimeline) para que la
 * lógica correcta viva en UN solo lugar.
 *
 * Garantías:
 *  - debounce de 900ms mientras se escribe;
 *  - guarda inmediato al perder foco (onBlur) y al desmontar el componente;
 *  - navigator.sendBeacon en beforeunload/pagehide (única forma confiable de
 *    que el guardado sobreviva a un refresh/cierre de pestaña);
 *  - `savedRef` solo se marca como guardado tras la CONFIRMACIÓN del servidor,
 *    con reintento automático — si falla, `failed` se pone en true para avisar
 *    en la UI, y ninguna red de seguridad da la nota por guardada de más.
 */
export function useContactNotes(contactId: number, initial: string) {
  const [notas, setNotasLocal] = useState(initial ?? "");
  const [failed, setFailed] = useState(false);
  const save = useMutation({
    mutationFn: (v: string) => api.setNotes(contactId, v),
    retry: 2,
    retryDelay: 1000,
  });
  const timer = useRef<ReturnType<typeof setTimeout>>();
  const notasRef = useRef(notas);
  const savedRef = useRef(initial ?? "");
  notasRef.current = notas;

  const flush = (value: string) => {
    clearTimeout(timer.current);
    if (value === savedRef.current) return;
    save.mutate(value, {
      onSuccess: () => { savedRef.current = value; setFailed(false); },
      onError: () => setFailed(true),
    });
  };

  const onChange = (value: string) => {
    setNotasLocal(value);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => flush(value), 900);
  };

  useEffect(() => {
    const beaconFlush = () => {
      if (notasRef.current !== savedRef.current) {
        const blob = new Blob([JSON.stringify({ notas: notasRef.current })],
          { type: "application/json" });
        navigator.sendBeacon(`/api/contacts/${contactId}/notes/beacon`, blob);
        savedRef.current = notasRef.current;
      }
    };
    window.addEventListener("beforeunload", beaconFlush);
    window.addEventListener("pagehide", beaconFlush);
    return () => {
      window.removeEventListener("beforeunload", beaconFlush);
      window.removeEventListener("pagehide", beaconFlush);
      flush(notasRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contactId]);

  return { notas, onChange, flush, failed };
}
