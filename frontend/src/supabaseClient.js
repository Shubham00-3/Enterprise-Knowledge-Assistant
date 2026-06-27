import { createClient } from "@supabase/supabase-js";

const url = import.meta.env.VITE_SUPABASE_URL;
const anonKey = import.meta.env.VITE_SUPABASE_ANON_KEY;

// Auth is opt-in: with no Supabase env vars the app runs anonymously against the
// shared seed pool (unchanged demo). With them set, users sign in and get their own space.
export const authEnabled = Boolean(url && anonKey);
export const supabase = authEnabled ? createClient(url, anonKey) : null;
