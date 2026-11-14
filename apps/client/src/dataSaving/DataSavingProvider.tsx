import {
  createContext,
  ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { readSettings, writeSettings } from "./storage";
import { DataSavingSettings, DEFAULT_SETTINGS } from "./types";

type DataSaving = DataSavingSettings & {
  /** False until the saved choices have been read. Until then everything is off. */
  ready: boolean;
  change(next: Partial<DataSavingSettings>): void;
};

// Without a provider (tests of other screens) data saving is off and cannot be changed.
const DataSavingContext = createContext<DataSaving>({ ...DEFAULT_SETTINGS, ready: true, change: () => {} });

export function DataSavingProvider({ children }: { children: ReactNode }) {
  const [settings, setSettings] = useState<DataSavingSettings>(DEFAULT_SETTINGS);
  const [ready, setReady] = useState(false);
  const changed = useRef(false);

  useEffect(() => {
    let live = true;
    void readSettings().then((saved) => {
      // A change made before the read finished wins over the saved copy.
      if (live && !changed.current) {
        setSettings(saved);
      }
      if (live) {
        setReady(true);
      }
    });
    return () => {
      live = false;
    };
  }, []);

  const change = useCallback((next: Partial<DataSavingSettings>) => {
    changed.current = true;
    setSettings((current) => {
      const updated = { ...current, ...next };
      void writeSettings(updated);
      return updated;
    });
  }, []);

  const value = useMemo(() => ({ ...settings, ready, change }), [settings, ready, change]);
  return <DataSavingContext.Provider value={value}>{children}</DataSavingContext.Provider>;
}

export function useDataSaving(): DataSaving {
  return useContext(DataSavingContext);
}
