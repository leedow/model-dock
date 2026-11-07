import {
  createContext,
  useContext,
  useState,
  useEffect,
  type ReactNode,
} from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { message } from "antd";
import { useTranslation } from "react-i18next";
import { api, json, setCSRF } from "./api";
import type { User } from "./types";

interface Auth {
  user: User | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}
const AuthContext = createContext<Auth>(null!);
export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null),
    [loading, setLoading] = useState(true);
  const qc = useQueryClient();
  useEffect(() => {
    let active = true;
    const unauthorized = () => {
      setUser(null);
      setCSRF("");
      qc.clear();
    };
    window.addEventListener("modeldock:unauthorized", unauthorized);
    api("/api/v1/auth/me")
      .then((result) => {
        if (active) {
          setUser(result.user);
          setCSRF(result.csrf_token);
        }
      })
      .catch(() => {})
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
      window.removeEventListener("modeldock:unauthorized", unauthorized);
    };
  }, [qc]);
  const login = async (username: string, password: string) => {
    const data = await api(
      "/api/v1/auth/login",
      json("POST", { username, password }),
    );
    qc.clear();
    setCSRF(data.csrf_token);
    setUser(data.user);
  };
  const logout = async () => {
    await api("/api/v1/auth/logout", json("POST"));
    setCSRF("");
    setUser(null);
    qc.clear();
  };
  return (
    <AuthContext.Provider value={{ user, loading, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}
export const useAuth = () => useContext(AuthContext);
export function useRows<T>(
  path: string,
  params: Record<string, string | number | undefined> = {},
  poll = 5000,
) {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== "") search.set(key, String(value));
  });
  const url = path + (search.size ? "?" + search : "");
  return useQuery<{ data: T[]; total?: number }>({
    queryKey: [url],
    queryFn: () => api(url),
    refetchInterval: poll || false,
  });
}
export function useAction<T = any>(
  fn: (input: T) => Promise<unknown>,
  onSuccess?: () => void,
) {
  const qc = useQueryClient();
  const { t } = useTranslation();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      void qc.invalidateQueries();
      void message.success(t("operationSuccess"));
      onSuccess?.();
    },
    onError: (error: Error) => {
      void message.error(error.message);
    },
  });
}
