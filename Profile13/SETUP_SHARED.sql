-- NEW PROJECT ONLY. Run once in Supabase SQL editor. No credentials belong here.
BEGIN;

-- 001_archive.sql
-- Apply once using the Supabase SQL editor as the project administrator.
CREATE TABLE public.search_history (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES auth.users(id),
    device_id uuid NOT NULL,
    query text NOT NULL CHECK (char_length(query) BETWEEN 1 AND 2000),
    searched_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    source text NOT NULL DEFAULT 'chrome_android' CHECK (source IN ('chrome_android', 'chrome_android_share')),
    domain text NOT NULL CHECK (domain IN ('google.com','www.google.com','google.lk','www.google.lk',
        'google.co.uk','www.google.co.uk','google.co.in','www.google.co.in',
        'bing.com','www.bing.com','cn.bing.com','duckduckgo.com','www.duckduckgo.com',
        'html.duckduckgo.com','lite.duckduckgo.com','search.brave.com',
        'ecosia.org','www.ecosia.org','search.yahoo.com')),
    capture_version integer NOT NULL DEFAULT 1 CHECK (capture_version = 1)
);
CREATE INDEX search_history_user_time_idx ON public.search_history(user_id, searched_at DESC);
ALTER TABLE public.search_history ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.search_history FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT ON public.search_history TO authenticated;
CREATE POLICY read_own_searches ON public.search_history FOR SELECT TO authenticated
    USING ((SELECT auth.uid()) = user_id);
CREATE POLICY insert_own_searches ON public.search_history FOR INSERT TO authenticated
    WITH CHECK ((SELECT auth.uid()) = user_id);

-- Invoker security preserves RLS. A retry is accepted only if its original immutable
-- payload belongs to this user and exactly matches. No UPDATE or DELETE grant is needed.
CREATE FUNCTION public.archive_searches(records jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $$
DECLARE
    item jsonb;
    result jsonb := '[]'::jsonb;
    record_id uuid;
BEGIN
    IF auth.uid() IS NULL THEN RAISE EXCEPTION 'authentication required' USING ERRCODE = '42501'; END IF;
    IF jsonb_typeof(records) IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'expected array'; END IF;
    IF jsonb_array_length(records) NOT BETWEEN 1 AND 100 THEN RAISE EXCEPTION 'batch size must be 1..100'; END IF;
    FOR item IN SELECT value FROM jsonb_array_elements(records) LOOP
        record_id := (item->>'id')::uuid;
        INSERT INTO public.search_history(id, user_id, device_id, query, searched_at, source, domain, capture_version)
        VALUES(record_id, auth.uid(), (item->>'device_id')::uuid, item->>'query',
            (item->>'searched_at')::timestamptz, item->>'source', item->>'domain', (item->>'capture_version')::integer)
        ON CONFLICT(id) DO NOTHING;
        IF NOT EXISTS (SELECT 1 FROM public.search_history h WHERE h.id = record_id AND h.user_id = auth.uid()
            AND h.device_id = (item->>'device_id')::uuid AND h.query = item->>'query'
            AND h.searched_at = (item->>'searched_at')::timestamptz AND h.source = item->>'source'
            AND h.domain = item->>'domain' AND h.capture_version = (item->>'capture_version')::integer) THEN
            RAISE EXCEPTION 'record conflict' USING ERRCODE = '23505';
        END IF;
        result := result || jsonb_build_array(record_id::text);
    END LOOP;
    RETURN result;
END;
$$;
REVOKE ALL ON FUNCTION public.archive_searches(jsonb) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.archive_searches(jsonb) TO authenticated;

-- 003_shared_archive.sql
-- Apply after 001 (and 002 on an older deployment). Keeps existing rows and ownership rules.
ALTER TABLE public.search_history ALTER COLUMN received_at SET DEFAULT clock_timestamp();
ALTER TABLE public.search_history DROP CONSTRAINT search_history_source_check;
ALTER TABLE public.search_history ADD CONSTRAINT search_history_source_check
    CHECK (source IN ('chrome_android','chrome_android_share','chrome_desktop_profile13'));

CREATE FUNCTION public.read_archive_page(after_received timestamptz DEFAULT NULL,
    after_id uuid DEFAULT NULL, page_size integer DEFAULT 100) RETURNS jsonb
LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $$
DECLARE result jsonb;
BEGIN
    IF auth.uid() IS NULL THEN RAISE EXCEPTION 'authentication required' USING ERRCODE = '42501'; END IF;
    IF page_size IS NULL OR page_size NOT BETWEEN 1 AND 100 THEN RAISE EXCEPTION 'page size must be 1..100'; END IF;
    IF (after_received IS NULL) <> (after_id IS NULL) THEN RAISE EXCEPTION 'incomplete cursor'; END IF;
    SELECT coalesce(jsonb_agg(to_jsonb(page) ORDER BY page.received_at, page.id), '[]'::jsonb)
    INTO result FROM (
        SELECT h.id,h.device_id,h.query,h.domain,h.searched_at,h.source,h.capture_version,h.received_at
        FROM public.search_history h
        WHERE h.user_id = auth.uid() AND
            (after_received IS NULL OR (h.received_at,h.id) > (after_received,after_id))
        ORDER BY h.received_at,h.id LIMIT page_size
    ) page;
    RETURN result;
END; $$;
REVOKE ALL ON FUNCTION public.read_archive_page(timestamptz,uuid,integer) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.read_archive_page(timestamptz,uuid,integer) TO authenticated;
CREATE INDEX search_history_user_received_idx ON public.search_history(user_id,received_at,id);

-- 004_browser_history.sql
-- Apply after 003_shared_archive.sql. Full HTTP(S) visits observed by desktop Profile 13.
CREATE TABLE public.browser_history (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES auth.users(id),
    device_id uuid NOT NULL,
    url text NOT NULL CHECK (char_length(url) BETWEEN 1 AND 32768 AND url ~* '^https?://'),
    title text NOT NULL CHECK (char_length(title) <= 4096),
    domain text NOT NULL CHECK (char_length(domain) BETWEEN 1 AND 253),
    visited_at timestamptz NOT NULL,
    source text NOT NULL CHECK (source = 'chrome_desktop_profile13'),
    received_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
ALTER TABLE public.browser_history ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.browser_history FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT ON public.browser_history TO authenticated;
CREATE POLICY read_own_visits ON public.browser_history FOR SELECT TO authenticated
    USING ((SELECT auth.uid()) = user_id);
CREATE POLICY insert_own_visits ON public.browser_history FOR INSERT TO authenticated
    WITH CHECK ((SELECT auth.uid()) = user_id);
CREATE INDEX browser_history_user_received_idx ON public.browser_history(user_id,received_at,id);
CREATE INDEX browser_history_user_visited_idx ON public.browser_history(user_id,visited_at DESC);

CREATE FUNCTION public.archive_visits(records jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $$
DECLARE item jsonb; result jsonb := '[]'::jsonb; record_id uuid;
BEGIN
    IF auth.uid() IS NULL THEN RAISE EXCEPTION 'authentication required' USING ERRCODE = '42501'; END IF;
    IF jsonb_typeof(records) IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'expected array'; END IF;
    IF jsonb_array_length(records) NOT BETWEEN 1 AND 100 THEN RAISE EXCEPTION 'batch size must be 1..100'; END IF;
    FOR item IN SELECT value FROM jsonb_array_elements(records) LOOP
        record_id := (item->>'id')::uuid;
        INSERT INTO public.browser_history(id,user_id,device_id,url,title,domain,visited_at,source)
        VALUES(record_id,auth.uid(),(item->>'device_id')::uuid,item->>'url',item->>'title',item->>'domain',
            (item->>'visited_at')::timestamptz,item->>'source') ON CONFLICT(id) DO NOTHING;
        IF NOT EXISTS (SELECT 1 FROM public.browser_history h WHERE h.id=record_id AND h.user_id=auth.uid()
            AND h.device_id=(item->>'device_id')::uuid AND h.url=item->>'url' AND h.title=item->>'title'
            AND h.domain=item->>'domain' AND h.visited_at=(item->>'visited_at')::timestamptz AND h.source=item->>'source') THEN
            RAISE EXCEPTION 'record conflict' USING ERRCODE = '23505';
        END IF;
        result := result || jsonb_build_array(record_id::text);
    END LOOP;
    RETURN result;
END; $$;
REVOKE ALL ON FUNCTION public.archive_visits(jsonb) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.archive_visits(jsonb) TO authenticated;

CREATE FUNCTION public.read_visits_page(after_received timestamptz DEFAULT NULL,
    after_id uuid DEFAULT NULL, page_size integer DEFAULT 100) RETURNS jsonb
LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $$
DECLARE result jsonb;
BEGIN
    IF auth.uid() IS NULL THEN RAISE EXCEPTION 'authentication required' USING ERRCODE = '42501'; END IF;
    IF page_size IS NULL OR page_size NOT BETWEEN 1 AND 100 THEN RAISE EXCEPTION 'page size must be 1..100'; END IF;
    IF (after_received IS NULL) <> (after_id IS NULL) THEN RAISE EXCEPTION 'incomplete cursor'; END IF;
    SELECT coalesce(jsonb_agg(to_jsonb(page) ORDER BY page.received_at,page.id),'[]'::jsonb) INTO result FROM (
        SELECT h.id,h.device_id,h.url,h.title,h.domain,h.visited_at,h.source,h.received_at
        FROM public.browser_history h WHERE h.user_id=auth.uid()
            AND (after_received IS NULL OR (h.received_at,h.id) > (after_received,after_id))
        ORDER BY h.received_at,h.id LIMIT page_size
    ) page;
    RETURN result;
END; $$;
REVOKE ALL ON FUNCTION public.read_visits_page(timestamptz,uuid,integer) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.read_visits_page(timestamptz,uuid,integer) TO authenticated;

-- 005_temporary_relay.sql
-- Temporary mobile relay. Desktop commits and verifies local records before consuming.
DROP POLICY insert_own_searches ON public.search_history;
CREATE POLICY insert_own_searches ON public.search_history FOR INSERT TO authenticated
    WITH CHECK ((SELECT auth.uid()) = user_id AND source IN ('chrome_android','chrome_android_share'));
REVOKE INSERT ON public.browser_history FROM authenticated;
REVOKE EXECUTE ON FUNCTION public.archive_visits(jsonb) FROM authenticated;
GRANT DELETE ON public.search_history, public.browser_history TO authenticated;
CREATE POLICY consume_own_searches ON public.search_history FOR DELETE TO authenticated
    USING ((SELECT auth.uid()) = user_id);
CREATE POLICY consume_own_visits ON public.browser_history FOR DELETE TO authenticated
    USING ((SELECT auth.uid()) = user_id);

CREATE FUNCTION public.consume_archive_records(record_kind text, records jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $$
DECLARE item jsonb; saved jsonb; record_id uuid; result jsonb := '[]'::jsonb;
BEGIN
    IF auth.uid() IS NULL THEN RAISE EXCEPTION 'authentication required' USING ERRCODE='42501'; END IF;
    IF record_kind IS NULL OR record_kind NOT IN ('search','visit') THEN RAISE EXCEPTION 'invalid record kind'; END IF;
    IF jsonb_typeof(records) IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'expected array'; END IF;
    IF jsonb_array_length(records) NOT BETWEEN 1 AND 100 THEN RAISE EXCEPTION 'batch size must be 1..100'; END IF;
    FOR item IN SELECT value FROM jsonb_array_elements(records) LOOP
        record_id := (item->>'id')::uuid;
        IF record_id IS NULL THEN RAISE EXCEPTION 'missing record id'; END IF;
        saved := NULL;
        IF record_kind = 'search' THEN
            SELECT to_jsonb(h)-'user_id' INTO saved FROM public.search_history h
                WHERE h.id=record_id AND h.user_id=auth.uid();
        ELSE
            SELECT to_jsonb(h)-'user_id' INTO saved FROM public.browser_history h
                WHERE h.id=record_id AND h.user_id=auth.uid();
        END IF;
        IF saved IS NOT NULL AND saved IS DISTINCT FROM item THEN
            RAISE EXCEPTION 'payload changed; cloud copy retained' USING ERRCODE='23505';
        END IF;
        IF record_kind = 'search' THEN
            DELETE FROM public.search_history h WHERE h.id=record_id AND h.user_id=auth.uid()
                AND to_jsonb(h)-'user_id'=item;
            IF EXISTS (SELECT 1 FROM public.search_history h WHERE h.id=record_id AND h.user_id=auth.uid()) THEN
                RAISE EXCEPTION 'record changed during consumption' USING ERRCODE='23505';
            END IF;
        ELSE
            DELETE FROM public.browser_history h WHERE h.id=record_id AND h.user_id=auth.uid()
                AND to_jsonb(h)-'user_id'=item;
            IF EXISTS (SELECT 1 FROM public.browser_history h WHERE h.id=record_id AND h.user_id=auth.uid()) THEN
                RAISE EXCEPTION 'record changed during consumption' USING ERRCODE='23505';
            END IF;
        END IF;
        result := result || jsonb_build_array(record_id::text);
    END LOOP;
    RETURN result;
END; $$;
REVOKE ALL ON FUNCTION public.consume_archive_records(text,jsonb) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.consume_archive_records(text,jsonb) TO authenticated;

-- 006_mobile_browsing.sql
-- Full observed mobile HTTP(S) browsing records, still a temporary relay.
ALTER TABLE public.browser_history DROP CONSTRAINT browser_history_source_check;
ALTER TABLE public.browser_history ADD CONSTRAINT browser_history_source_check
    CHECK (source IN ('chrome_desktop_profile13','chrome_android','chrome_android_share'));
ALTER TABLE public.browser_history ADD COLUMN observed_url text NOT NULL DEFAULT '' CHECK (char_length(observed_url)<=32768);
ALTER TABLE public.browser_history ADD COLUMN url_quality text NOT NULL DEFAULT 'legacy'
    CHECK (url_quality IN ('legacy','document','address_bar','inferred_scheme','shared'));
ALTER TABLE public.browser_history ADD COLUMN title_source text NOT NULL DEFAULT 'unavailable'
    CHECK (title_source IN ('unavailable','pane','window','share'));
ALTER TABLE public.browser_history ADD COLUMN tab_mode text NOT NULL DEFAULT 'unknown'
    CHECK (tab_mode IN ('normal','incognito','unknown'));
ALTER TABLE public.browser_history ADD COLUMN capture_version integer NOT NULL DEFAULT 1 CHECK (capture_version IN (1,2));
ALTER TABLE public.browser_history ADD CONSTRAINT mobile_visit_metadata_check
    CHECK (source='chrome_desktop_profile13' OR
        (capture_version=2 AND char_length(observed_url)>=1 AND url_quality<>'legacy'));
DROP POLICY insert_own_visits ON public.browser_history;
CREATE POLICY insert_own_visits ON public.browser_history FOR INSERT TO authenticated
    WITH CHECK ((SELECT auth.uid())=user_id AND source IN ('chrome_android','chrome_android_share') AND capture_version=2);
GRANT INSERT ON public.browser_history TO authenticated;

CREATE FUNCTION public.archive_mobile_visits(records jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY INVOKER SET search_path='' AS $$
DECLARE item jsonb; record_id uuid; result jsonb := '[]'::jsonb;
BEGIN
    IF auth.uid() IS NULL THEN RAISE EXCEPTION 'authentication required' USING ERRCODE='42501'; END IF;
    IF jsonb_typeof(records) IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'expected array'; END IF;
    IF jsonb_array_length(records) NOT BETWEEN 1 AND 100 THEN RAISE EXCEPTION 'batch size must be 1..100'; END IF;
    FOR item IN SELECT value FROM jsonb_array_elements(records) LOOP
        record_id := (item->>'id')::uuid;
        INSERT INTO public.browser_history(id,user_id,device_id,url,title,domain,visited_at,source,
            observed_url,url_quality,title_source,tab_mode,capture_version)
        VALUES(record_id,auth.uid(),(item->>'device_id')::uuid,item->>'url',item->>'title',item->>'domain',
            (item->>'visited_at')::timestamptz,item->>'source',item->>'observed_url',item->>'url_quality',
            item->>'title_source',item->>'tab_mode',(item->>'capture_version')::integer)
        ON CONFLICT(id) DO NOTHING;
        IF NOT EXISTS (SELECT 1 FROM public.browser_history h WHERE h.id=record_id AND h.user_id=auth.uid()
            AND h.visited_at=(item->>'visited_at')::timestamptz AND
            to_jsonb(h)-ARRAY['user_id','received_at','visited_at'] = item-'visited_at') THEN
            RAISE EXCEPTION 'immutable visit conflict' USING ERRCODE='23505';
        END IF;
        result := result || jsonb_build_array(record_id::text);
    END LOOP;
    RETURN result;
END; $$;
REVOKE ALL ON FUNCTION public.archive_mobile_visits(jsonb) FROM PUBLIC,anon;
GRANT EXECUTE ON FUNCTION public.archive_mobile_visits(jsonb) TO authenticated;

CREATE OR REPLACE FUNCTION public.read_visits_page(after_received timestamptz DEFAULT NULL,
    after_id uuid DEFAULT NULL,page_size integer DEFAULT 100) RETURNS jsonb
LANGUAGE plpgsql SECURITY INVOKER SET search_path='' AS $$
DECLARE result jsonb;
BEGIN
    IF auth.uid() IS NULL THEN RAISE EXCEPTION 'authentication required' USING ERRCODE='42501'; END IF;
    IF page_size IS NULL OR page_size NOT BETWEEN 1 AND 100 THEN RAISE EXCEPTION 'page size must be 1..100'; END IF;
    IF (after_received IS NULL)<>(after_id IS NULL) THEN RAISE EXCEPTION 'incomplete cursor'; END IF;
    SELECT coalesce(jsonb_agg(to_jsonb(page) ORDER BY page.received_at,page.id),'[]'::jsonb) INTO result FROM (
        SELECT h.id,h.device_id,h.url,h.title,h.domain,h.visited_at,h.source,h.observed_url,h.url_quality,
            h.title_source,h.tab_mode,h.capture_version,h.received_at
        FROM public.browser_history h WHERE h.user_id=auth.uid() AND
            (after_received IS NULL OR (h.received_at,h.id)>(after_received,after_id))
        ORDER BY h.received_at,h.id LIMIT page_size
    ) page;
    RETURN result;
END; $$;

COMMIT;
