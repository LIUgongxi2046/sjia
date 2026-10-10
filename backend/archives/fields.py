from core.fields import FiniteDecimalField


class NumericField(FiniteDecimalField):
    def db_type(self, connection):
        return 'numeric'
