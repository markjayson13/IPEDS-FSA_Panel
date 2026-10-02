import importlib.util
from pathlib import Path
import sys
import unittest

SCRIPTS=Path(__file__).resolve().parents[1]/"Scripts"
sys.path.insert(0,str(SCRIPTS))
spec=importlib.util.spec_from_file_location("builder",SCRIPTS/"16_build_fsa_ipeds_panel.py")
builder=importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)

class LineageBindingTests(unittest.TestCase):
    def test_table_drift_rejected(self):
        with self.assertRaisesRegex(ValueError,"cache differs"):
            builder.validate_lineage_cache([{"year":2023,"table":"P1"}],[{"year":2023,"table":"P2"}])

    def test_missing_year_rejected(self):
        with self.assertRaises(ValueError):
            builder.validate_lineage_cache([{"year":2022},{"year":2023}],[{"year":2023}])

    def test_extra_field_rejected(self):
        with self.assertRaises(ValueError):
            builder.validate_lineage_cache([{"year":2023}],[{"year":2023,"table":"P1"}])

    def test_order_and_duplicate_insensitive(self):
        builder.validate_lineage_cache([{"year":2022},{"year":2023}],[{"year":2023},{"year":2022},{"year":2022}])

if __name__=="__main__":unittest.main()
