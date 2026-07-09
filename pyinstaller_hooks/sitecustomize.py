try:
    import numpy as np

    if not hasattr(np, "NaN"):
        np.NaN = np.nan
    if not hasattr(np, "NAN"):
        np.NAN = np.nan
except Exception:
    pass
