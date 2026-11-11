"use client";

import { createContext, useContext, useMemo, useState } from "react";

/**
 * The selected instrument.
 *
 * A tiny context rather than a global store: the selection is the only thing
 * that crosses the server/client boundary between the watchlist and the
 * decision center.
 */
const SymbolContext = createContext<{
  selected: string | null;
  setSelected: (symbol: string) => void;
}>({ selected: null, setSelected: () => {} });

export function SymbolProvider({
  children,
  initial,
}: {
  children: React.ReactNode;
  initial: string | null;
}) {
  const [selected, setSelected] = useState<string | null>(initial);
  const value = useMemo(() => ({ selected, setSelected }), [selected]);
  return <SymbolContext.Provider value={value}>{children}</SymbolContext.Provider>;
}

export function useSymbol() {
  return useContext(SymbolContext);
}
