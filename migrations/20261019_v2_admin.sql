-- TEST FIRST. Review current production schema before executing there.
-- Requires public.stores and Supabase auth.users.
BEGIN;
ALTER TABLE public.stores ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active';
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'stores_status_valid_v2' AND conrelid='public.stores'::regclass) THEN
    ALTER TABLE public.stores ADD CONSTRAINT stores_status_valid_v2 CHECK (status IN ('active','suspended'));
  END IF;
END $$;
CREATE TABLE IF NOT EXISTS public.platform_admins (
 user_id UUID PRIMARY KEY REFERENCES auth.users(id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS public.platform_admin_logs (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 admin_user_id UUID NOT NULL REFERENCES auth.users(id),
 store_id UUID NOT NULL REFERENCES public.stores(id),
 action TEXT NOT NULL CHECK (action IN ('STORE_SUSPENDED','STORE_REACTIVATED')),
 previous_status TEXT NOT NULL,
 new_status TEXT NOT NULL,
 reason TEXT NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_admin_logs_created ON public.platform_admin_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_admin_logs_store ON public.platform_admin_logs(store_id,created_at DESC);
ALTER TABLE public.platform_admins ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.platform_admin_logs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.platform_admins FROM anon, authenticated;
REVOKE ALL ON public.platform_admin_logs FROM anon, authenticated;
COMMIT;
-- Do NOT insert admin identities from code or automate granting super-admin privileges.
