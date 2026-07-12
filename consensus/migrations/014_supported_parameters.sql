-- Tool-capability data from OpenRouter (issue #23): comma-separated
-- supported_parameters list; '' = unknown (row predates this column).
ALTER TABLE model_pricing ADD COLUMN supported_parameters TEXT DEFAULT '';
