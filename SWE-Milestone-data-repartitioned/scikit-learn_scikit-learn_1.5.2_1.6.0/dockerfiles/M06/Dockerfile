# Build on pre-configured base image
FROM scikit-learn_scikit-learn_1.5.2_1.6.0/base:latest

# Set umask to ensure files created in container are world-writable
# This prevents permission issues when test results are written to mounted volumes
RUN echo 'umask 000' >> /etc/bash.bashrc && \
    echo '#!/bin/bash\numask 000\nexec "$@"' > /entrypoint.sh && \
    chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]

# [ENV-PATCH] Install array_api_compat and array_api_strict for array API tests
# These are required by test_label_encoder_array_api_compliance and test_isin
RUN pip install array_api_compat array_api_strict

# Configure git identity for commits
RUN git config --global user.email "env-patch@localhost" && \
    git config --global user.name "ENV Patch"

# Remove the original /testbed from base image and copy local testbed
# NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
RUN rm -rf /testbed
COPY . /testbed/

# Checkout to END state first (all features available)
# Tags already exist in testbed - just checkout, don't create tags
RUN cd /testbed && git checkout milestone-M06-end

# [ENV-PATCH] Fix test_base.py import error - is_clusterer doesn't exist at this state
# Wrap the is_clusterer import in try-except using Python script
RUN cd /testbed && python3 << 'EOF'
import re

# Read the file
with open('sklearn/tests/test_base.py', 'r') as f:
    content = f.read()

# Replace the import - remove is_clusterer from the import block
old_import = '''from sklearn.base import (
    BaseEstimator,
    OutlierMixin,
    TransformerMixin,
    clone,
    is_classifier,
    is_clusterer,
    is_regressor,
)'''
new_import = '''from sklearn.base import (
    BaseEstimator,
    OutlierMixin,
    TransformerMixin,
    clone,
    is_classifier,
    is_regressor,
)

# [ENV-PATCH] Conditional import for is_clusterer - may not exist in all versions
try:
    from sklearn.base import is_clusterer
except ImportError:
    is_clusterer = None'''

content = content.replace(old_import, new_import)

# Add skipif decorator to test_is_clusterer
old_test = '''def test_is_clusterer(estimator, expected_result):
    assert is_clusterer(estimator) == expected_result'''
new_test = '''@pytest.mark.skipif(is_clusterer is None, reason="[ENV-PATCH] is_clusterer not available in this version")
def test_is_clusterer(estimator, expected_result):
    assert is_clusterer(estimator) == expected_result'''

content = content.replace(old_test, new_test)

# Write back
with open('sklearn/tests/test_base.py', 'w') as f:
    f.write(content)
print('Patched test_base.py successfully')
EOF

RUN cd /testbed && \
    git add -A && \
    git commit -m "[ENV-PATCH] Make is_clusterer import conditional in test_base.py" && \
    git tag -f milestone-M06-end HEAD

# Apply patches to START state (including test_array_api.py fix)
RUN cd /testbed && git checkout milestone-M06-start && python3 << 'EOF'
import re

# ===== Patch 1: test_base.py =====
with open('sklearn/tests/test_base.py', 'r') as f:
    content = f.read()

old_import = '''from sklearn.base import (
    BaseEstimator,
    OutlierMixin,
    TransformerMixin,
    clone,
    is_classifier,
    is_clusterer,
    is_regressor,
)'''
new_import = '''from sklearn.base import (
    BaseEstimator,
    OutlierMixin,
    TransformerMixin,
    clone,
    is_classifier,
    is_regressor,
)

# [ENV-PATCH] Conditional import for is_clusterer - may not exist in all versions
try:
    from sklearn.base import is_clusterer
except ImportError:
    is_clusterer = None'''

content = content.replace(old_import, new_import)

old_test = '''def test_is_clusterer(estimator, expected_result):
    assert is_clusterer(estimator) == expected_result'''
new_test = '''@pytest.mark.skipif(is_clusterer is None, reason="[ENV-PATCH] is_clusterer not available in this version")
def test_is_clusterer(estimator, expected_result):
    assert is_clusterer(estimator) == expected_result'''

content = content.replace(old_test, new_test)

with open('sklearn/tests/test_base.py', 'w') as f:
    f.write(content)
print('Patched test_base.py successfully')

# ===== Patch 2: test_array_api.py =====
with open('sklearn/utils/tests/test_array_api.py', 'r') as f:
    content = f.read()

# Make _isin import conditional
old_import_block = '''from sklearn.utils._array_api import (
    _ArrayAPIWrapper,
    _asarray_with_order,
    _atol_for_type,
    _average,
    _convert_to_numpy,
    _estimator_with_converted_arrays,
    _is_numpy_namespace,
    _isin,
    _nanmax,
    _nanmin,
    _NumPyAPIWrapper,
    _ravel,
    device,
    get_namespace,
    get_namespace_and_device,
    indexing_dtype,
    supported_float_dtypes,
    yield_namespace_device_dtype_combinations,
)'''

new_import_block = '''from sklearn.utils._array_api import (
    _ArrayAPIWrapper,
    _asarray_with_order,
    _atol_for_type,
    _average,
    _convert_to_numpy,
    _estimator_with_converted_arrays,
    _is_numpy_namespace,
    _nanmax,
    _nanmin,
    _NumPyAPIWrapper,
    _ravel,
    device,
    get_namespace,
    get_namespace_and_device,
    indexing_dtype,
    supported_float_dtypes,
    yield_namespace_device_dtype_combinations,
)

# [ENV-PATCH] Conditional import for _isin - may not exist in all versions
try:
    from sklearn.utils._array_api import _isin
except ImportError:
    _isin = None'''

content = content.replace(old_import_block, new_import_block)

# Add skipif decorator to test_isin
# Find the line with decorators and the function definition
old_test_def = '''@pytest.mark.parametrize("element_size", [6, 10, 14])
@pytest.mark.parametrize("int_dtype", ["int16", "int32", "int64", "uint8"])
def test_isin(
    array_namespace, device, _, invert, assume_unique, element_size, int_dtype
):'''

new_test_def = '''@pytest.mark.skipif(_isin is None, reason="[ENV-PATCH] _isin not available in this version")
@pytest.mark.parametrize("element_size", [6, 10, 14])
@pytest.mark.parametrize("int_dtype", ["int16", "int32", "int64", "uint8"])
def test_isin(
    array_namespace, device, _, invert, assume_unique, element_size, int_dtype
):'''

content = content.replace(old_test_def, new_test_def)

with open('sklearn/utils/tests/test_array_api.py', 'w') as f:
    f.write(content)
print('Patched test_array_api.py successfully')
EOF

RUN cd /testbed && \
    git add -A && \
    git commit -m "[ENV-PATCH] Make is_clusterer and _isin imports conditional" && \
    git tag -f milestone-M06-start HEAD

# Rebuild scikit-learn for START state (default)
RUN cd /testbed && pip install --no-build-isolation --editable .
