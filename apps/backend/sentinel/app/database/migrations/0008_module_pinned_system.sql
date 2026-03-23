-- Add pinned and system flags to modules

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'modules' AND column_name = 'pinned') THEN
        ALTER TABLE modules ADD COLUMN pinned BOOLEAN NOT NULL DEFAULT false;
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'modules' AND column_name = 'system') THEN
        ALTER TABLE modules ADD COLUMN system BOOLEAN NOT NULL DEFAULT false;
    END IF;
END $$;
