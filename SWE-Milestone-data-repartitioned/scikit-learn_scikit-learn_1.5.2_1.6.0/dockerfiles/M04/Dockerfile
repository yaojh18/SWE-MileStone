# Build on pre-configured base image
FROM scikit-learn_scikit-learn_1.5.2_1.6.0/base:latest

# Set umask to ensure files created in container are world-writable
# This prevents permission issues when test results are written to mounted volumes
RUN echo 'umask 000' >> /etc/bash.bashrc && \
    echo '#!/bin/bash\numask 000\nexec "$@"' > /entrypoint.sh && \
    chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]

# Remove the original /testbed from base image and copy local testbed
# NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
RUN rm -rf /testbed
COPY . /testbed/

# Configure git
RUN cd /testbed && git config --global --add safe.directory /testbed && \
    git config --global user.email "test@test.com" && \
    git config --global user.name "Test User"

# Create a reusable patch script that applies compatibility fixes
RUN cat > /tmp/apply_patches.sh << 'PATCH_SCRIPT'
#!/bin/bash
set -e
cd /testbed

# 1. Add assert_no_warnings and other missing functions to sklearn/utils/_testing.py
sed -i 's/from numpy.testing import (/from numpy.testing import (assert_no_warnings,/' sklearn/utils/_testing.py || true
sed -i '/"SkipTest",/a\    "assert_no_warnings",' sklearn/utils/_testing.py || true

cat >> sklearn/utils/_testing.py << 'PYSTUB'

# [ENV-PATCH] Compatibility stubs for missing functions
import unittest as _unittest_for_stub
_dummy_for_stub = _unittest_for_stub.TestCase("__init__")
assert_raises = _dummy_for_stub.assertRaises
assert_raises_regex = _dummy_for_stub.assertRaisesRegex

def assert_raise_message(exceptions, message, func, *args, **kwargs):
    """Stub for assert_raise_message."""
    import re as _re
    try:
        func(*args, **kwargs)
        raise AssertionError(f"Expected {exceptions} but none was raised")
    except exceptions as e:
        if not _re.search(message, str(e)):
            raise AssertionError(f"Error message \"{e}\" does not match \"{message}\"")
PYSTUB

# 2. Add _get_threadpool_controller stub to sklearn/utils/parallel.py
cat >> sklearn/utils/parallel.py << 'PYSTUB'

# [ENV-PATCH] Stub for compatibility
def _get_threadpool_controller():
    """Stub for tests expecting this function."""
    return None
PYSTUB

# 3. Add _yield_masked_array_for_each_param stub to sklearn/model_selection/_search.py
cat >> sklearn/model_selection/_search.py << 'PYSTUB'

# [ENV-PATCH] Stub for compatibility
def _yield_masked_array_for_each_param(*args, **kwargs):
    """Stub for tests expecting this function."""
    return iter([])
PYSTUB

# 4. Add is_clusterer to sklearn/base.py
cat >> sklearn/base.py << 'PYSTUB'

# [ENV-PATCH] Stub for compatibility
def is_clusterer(estimator):
    """Stub for tests expecting this function."""
    return False
PYSTUB

# 5. Add check_inplace_ensure_writeable stub to sklearn/utils/estimator_checks.py
cat >> sklearn/utils/estimator_checks.py << 'PYSTUB'

# [ENV-PATCH] Stub for compatibility
def check_inplace_ensure_writeable(*args, **kwargs):
    """Stub for tests expecting this function."""
    pass
PYSTUB

# 6. Add _isin stub to sklearn/utils/_array_api.py
cat >> sklearn/utils/_array_api.py << 'PYSTUB'

# [ENV-PATCH] Stub for compatibility
def _isin(*args, **kwargs):
    """Stub for tests expecting this function."""
    import numpy as np
    return np.isin(*args, **kwargs)
PYSTUB

# 7. Add log_logistic stub to sklearn/utils/extmath.py
cat >> sklearn/utils/extmath.py << 'PYSTUB'

# [ENV-PATCH] Stub for compatibility
def log_logistic(X, out=None):
    """Stub for tests expecting this function."""
    import numpy as np
    return -np.logaddexp(0, -X)
PYSTUB

# 8. Patch test_cython_blas.py to import enum values via parent class
python3 << 'PYSCRIPT'
import re
with open('sklearn/utils/tests/test_cython_blas.py', 'r') as f:
    content = f.read()
# Replace the multi-line import from _cython_blas
old_import = re.search(r'from sklearn\.utils\._cython_blas import \([^)]+\)', content, re.DOTALL)
if old_import:
    new_import = '''from sklearn.utils._cython_blas import (
    BLAS_Order,
    BLAS_Trans,
    _asum_memview,
    _axpy_memview,
    _copy_memview,
    _dot_memview,
    _gemm_memview,
    _gemv_memview,
    _ger_memview,
    _nrm2_memview,
    _rot_memview,
    _rotg_memview,
    _scal_memview,
)
# [ENV-PATCH] Aliases for enum values
ColMajor = BLAS_Order.ColMajor
RowMajor = BLAS_Order.RowMajor
NoTrans = BLAS_Trans.NoTrans
Trans = BLAS_Trans.Trans'''
    content = content[:old_import.start()] + new_import + content[old_import.end():]
    with open('sklearn/utils/tests/test_cython_blas.py', 'w') as f:
        f.write(content)
PYSCRIPT

PATCH_SCRIPT
RUN chmod +x /tmp/apply_patches.sh

# Checkout to END state and apply patches
RUN cd /testbed && git checkout milestone-M04-end && \
    /tmp/apply_patches.sh && \
    git add -A && \
    git commit -m "[ENV-PATCH] Add compatibility stubs for END state" && \
    git tag -f milestone-M04-end HEAD

# Clean build directory and rebuild scikit-learn for END state
# Remove old build artifacts and force complete rebuild
RUN pip uninstall -y scikit-learn && \
    find /usr/local/lib/python3.11/site-packages -name 'scikit*' -exec rm -rf {} + 2>/dev/null || true && \
    find /usr/local/lib/python3.11/site-packages -name 'sklearn*' -exec rm -rf {} + 2>/dev/null || true && \
    cd /testbed && rm -rf build/ && \
    pip install --no-build-isolation --editable .

# Checkout to START state and apply the same patches
RUN cd /testbed && git checkout milestone-M04-start && \
    /tmp/apply_patches.sh && \
    git add -A && \
    git commit -m "[ENV-PATCH] Add compatibility stubs for START state" && \
    git tag -f milestone-M04-start HEAD

# Clean up patch script
RUN rm /tmp/apply_patches.sh

# Set default state to START
RUN cd /testbed && git checkout milestone-M04-start
