-- Unified module schema: remove type/is_system, rename list_config → fields_config, add page_title/page_content

-- Rename list_config → fields_config
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'modules' AND column_name = 'list_config')
       AND NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'modules' AND column_name = 'fields_config')
    THEN
        ALTER TABLE modules RENAME COLUMN list_config TO fields_config;
    END IF;
END $$;

-- Add page_title
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'modules' AND column_name = 'page_title') THEN
        ALTER TABLE modules ADD COLUMN page_title VARCHAR NULL;
    END IF;
END $$;

-- Add page_content
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'modules' AND column_name = 'page_content') THEN
        ALTER TABLE modules ADD COLUMN page_content TEXT NULL;
    END IF;
END $$;

-- Drop type column
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'modules' AND column_name = 'type') THEN
        ALTER TABLE modules DROP COLUMN type;
    END IF;
END $$;

-- Drop is_system column
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'modules' AND column_name = 'is_system') THEN
        ALTER TABLE modules DROP COLUMN is_system;
    END IF;
END $$;
