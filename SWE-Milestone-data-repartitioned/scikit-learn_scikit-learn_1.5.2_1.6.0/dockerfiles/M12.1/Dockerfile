# Build on pre-configured base image
FROM scikit-learn_scikit-learn_1.5.2_1.6.0/base:latest

# Configure git identity for commits
RUN git config --global user.email "env-patch@milestone.local" && \
    git config --global user.name "Milestone Environment Patch"

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

# ============================================================================
# [ENV-PATCH] Create stub modules/functions to allow test collection
# These APIs are from OTHER milestones but test files import them
# Stubs raise NotImplementedError when called, but allow test collection
# ============================================================================

# Checkout to END state first to apply patches
RUN cd /testbed && git checkout milestone-M12.1-end

# 1. Create sklearn/utils/_test_common package (needed by test_ridge.py, test_pca.py, etc.)
RUN mkdir -p /testbed/sklearn/utils/_test_common && \
    echo '"""Stub module for _test_common package"""' > /testbed/sklearn/utils/_test_common/__init__.py && \
    cat > /testbed/sklearn/utils/_test_common/instance_generator.py << 'STUBEOF'
"""[ENV-PATCH] Stub module for instance_generator - API not in M12.1"""

def _get_check_estimator_ids(obj):
    """Returns string representation for pytest ids"""
    if hasattr(obj, '__name__'):
        return obj.__name__
    return str(obj)

def _construct_instances(*args, **kwargs):
    """Stub: not available in this milestone"""
    return []
STUBEOF

# 2. Add get_tags to sklearn/utils/__init__.py
RUN cd /testbed && \
    echo '' >> sklearn/utils/__init__.py && \
    echo '# [ENV-PATCH] Stub for get_tags - API not in M12.1' >> sklearn/utils/__init__.py && \
    echo 'def get_tags(estimator):' >> sklearn/utils/__init__.py && \
    echo '    """Stub: get_tags not available in this milestone"""' >> sklearn/utils/__init__.py && \
    echo '    from sklearn.utils._tags import _safe_tags' >> sklearn/utils/__init__.py && \
    echo '    return _safe_tags(estimator)' >> sklearn/utils/__init__.py

# 3. Add _yield_masked_array_for_each_param to sklearn/model_selection/_search.py
RUN cd /testbed && \
    echo '' >> sklearn/model_selection/_search.py && \
    echo '# [ENV-PATCH] Stub for _yield_masked_array_for_each_param - API not in M12.1' >> sklearn/model_selection/_search.py && \
    echo 'def _yield_masked_array_for_each_param(*args, **kwargs):' >> sklearn/model_selection/_search.py && \
    echo '    """Stub: _yield_masked_array_for_each_param not available in this milestone"""' >> sklearn/model_selection/_search.py && \
    echo '    raise NotImplementedError("_yield_masked_array_for_each_param not available in M12.1")' >> sklearn/model_selection/_search.py

# 4. Add is_clusterer to sklearn/base.py
RUN cd /testbed && \
    echo '' >> sklearn/base.py && \
    echo '# [ENV-PATCH] Stub for is_clusterer - API not in M12.1' >> sklearn/base.py && \
    echo 'def is_clusterer(estimator):' >> sklearn/base.py && \
    echo '    """Stub: is_clusterer not available in this milestone"""' >> sklearn/base.py && \
    echo '    raise NotImplementedError("is_clusterer not available in M12.1")' >> sklearn/base.py

# 5. Add check_classifier_not_supporting_multiclass to sklearn/utils/estimator_checks.py
RUN cd /testbed && \
    echo '' >> sklearn/utils/estimator_checks.py && \
    echo '# [ENV-PATCH] Stub for check_classifier_not_supporting_multiclass - API not in M12.1' >> sklearn/utils/estimator_checks.py && \
    echo 'def check_classifier_not_supporting_multiclass(*args, **kwargs):' >> sklearn/utils/estimator_checks.py && \
    echo '    """Stub: check_classifier_not_supporting_multiclass not available in this milestone"""' >> sklearn/utils/estimator_checks.py && \
    echo '    raise NotImplementedError("check_classifier_not_supporting_multiclass not available in M12.1")' >> sklearn/utils/estimator_checks.py

# 6. Add assert_docstring_consistency to sklearn/utils/_testing.py
RUN cd /testbed && \
    echo '' >> sklearn/utils/_testing.py && \
    echo '# [ENV-PATCH] Stub for assert_docstring_consistency - API not in M12.1' >> sklearn/utils/_testing.py && \
    echo 'def assert_docstring_consistency(*args, **kwargs):' >> sklearn/utils/_testing.py && \
    echo '    """Stub: assert_docstring_consistency not available in this milestone"""' >> sklearn/utils/_testing.py && \
    echo '    raise NotImplementedError("assert_docstring_consistency not available in M12.1")' >> sklearn/utils/_testing.py

# 7. Add validate_data and _check_n_features to sklearn/utils/validation.py
RUN cd /testbed && \
    echo '' >> sklearn/utils/validation.py && \
    echo '# [ENV-PATCH] Stub for validate_data - API not in M12.1' >> sklearn/utils/validation.py && \
    echo 'def validate_data(*args, **kwargs):' >> sklearn/utils/validation.py && \
    echo '    """Stub: validate_data not available in this milestone"""' >> sklearn/utils/validation.py && \
    echo '    raise NotImplementedError("validate_data not available in M12.1")' >> sklearn/utils/validation.py && \
    echo '' >> sklearn/utils/validation.py && \
    echo '# [ENV-PATCH] Stub for _check_n_features - API not in M12.1' >> sklearn/utils/validation.py && \
    echo 'def _check_n_features(*args, **kwargs):' >> sklearn/utils/validation.py && \
    echo '    """Stub: _check_n_features not available in this milestone"""' >> sklearn/utils/validation.py && \
    echo '    raise NotImplementedError("_check_n_features not available in M12.1")' >> sklearn/utils/validation.py

# 8. Add multiple stubs to sklearn/utils/estimator_checks.py
# These are needed for test_estimator_checks.py to collect
RUN cd /testbed && \
    cat >> sklearn/utils/estimator_checks.py << 'STUBEOF'

# [ENV-PATCH] Stub functions for test collection compatibility - APIs not in M12.1
class _NotAnArray:
    """Stub class for _NotAnArray - API not in M12.1"""
    pass

def check_estimator_cloneable(*args, **kwargs):
    """Stub: check_estimator_cloneable not available in this milestone"""
    raise NotImplementedError("check_estimator_cloneable not available in M12.1")

def check_estimator_repr(*args, **kwargs):
    """Stub: check_estimator_repr not available in this milestone"""
    raise NotImplementedError("check_estimator_repr not available in M12.1")

def check_estimator_tags_renamed(*args, **kwargs):
    """Stub: check_estimator_tags_renamed not available in this milestone"""
    raise NotImplementedError("check_estimator_tags_renamed not available in M12.1")
STUBEOF

# 9. Add skip_if_no_numpydoc decorator to sklearn/utils/_testing.py
RUN cd /testbed && \
    echo '' >> sklearn/utils/_testing.py && \
    echo '# [ENV-PATCH] Stub for skip_if_no_numpydoc - API not in M12.1' >> sklearn/utils/_testing.py && \
    echo 'import pytest as _pytest' >> sklearn/utils/_testing.py && \
    echo 'def skip_if_no_numpydoc(func):' >> sklearn/utils/_testing.py && \
    echo '    """Stub decorator: skip_if_no_numpydoc not available in this milestone"""' >> sklearn/utils/_testing.py && \
    echo '    return _pytest.mark.skip(reason="[ENV-PATCH] skip_if_no_numpydoc not available")(func)' >> sklearn/utils/_testing.py

# Commit patches to END state and move tag
RUN cd /testbed && \
    git add -A && \
    git commit -m "[ENV-PATCH] Add stub modules for test collection compatibility" && \
    git tag -f milestone-M12.1-end HEAD

# Clean build directory and rebuild scikit-learn for END state
RUN cd /testbed && rm -rf build/ && pip install --no-build-isolation --editable .

# ============================================================================
# Now apply same patches to START state
# ============================================================================

RUN cd /testbed && git checkout milestone-M12.1-start

# Apply the same stubs to START state
# 1. Create sklearn/utils/_test_common package
RUN mkdir -p /testbed/sklearn/utils/_test_common && \
    echo '"""Stub module for _test_common package"""' > /testbed/sklearn/utils/_test_common/__init__.py && \
    cat > /testbed/sklearn/utils/_test_common/instance_generator.py << 'STUBEOF'
"""[ENV-PATCH] Stub module for instance_generator - API not in M12.1"""

def _get_check_estimator_ids(obj):
    """Returns string representation for pytest ids"""
    if hasattr(obj, '__name__'):
        return obj.__name__
    return str(obj)

def _construct_instances(*args, **kwargs):
    """Stub: not available in this milestone"""
    return []
STUBEOF

# 2. Add get_tags to sklearn/utils/__init__.py
RUN cd /testbed && \
    echo '' >> sklearn/utils/__init__.py && \
    echo '# [ENV-PATCH] Stub for get_tags - API not in M12.1' >> sklearn/utils/__init__.py && \
    echo 'def get_tags(estimator):' >> sklearn/utils/__init__.py && \
    echo '    """Stub: get_tags not available in this milestone"""' >> sklearn/utils/__init__.py && \
    echo '    from sklearn.utils._tags import _safe_tags' >> sklearn/utils/__init__.py && \
    echo '    return _safe_tags(estimator)' >> sklearn/utils/__init__.py

# 3. Add _yield_masked_array_for_each_param to sklearn/model_selection/_search.py
RUN cd /testbed && \
    echo '' >> sklearn/model_selection/_search.py && \
    echo '# [ENV-PATCH] Stub for _yield_masked_array_for_each_param - API not in M12.1' >> sklearn/model_selection/_search.py && \
    echo 'def _yield_masked_array_for_each_param(*args, **kwargs):' >> sklearn/model_selection/_search.py && \
    echo '    """Stub: _yield_masked_array_for_each_param not available in this milestone"""' >> sklearn/model_selection/_search.py && \
    echo '    raise NotImplementedError("_yield_masked_array_for_each_param not available in M12.1")' >> sklearn/model_selection/_search.py

# 4. Add is_clusterer to sklearn/base.py
RUN cd /testbed && \
    echo '' >> sklearn/base.py && \
    echo '# [ENV-PATCH] Stub for is_clusterer - API not in M12.1' >> sklearn/base.py && \
    echo 'def is_clusterer(estimator):' >> sklearn/base.py && \
    echo '    """Stub: is_clusterer not available in this milestone"""' >> sklearn/base.py && \
    echo '    raise NotImplementedError("is_clusterer not available in M12.1")' >> sklearn/base.py

# 5. Add check_classifier_not_supporting_multiclass to sklearn/utils/estimator_checks.py
RUN cd /testbed && \
    echo '' >> sklearn/utils/estimator_checks.py && \
    echo '# [ENV-PATCH] Stub for check_classifier_not_supporting_multiclass - API not in M12.1' >> sklearn/utils/estimator_checks.py && \
    echo 'def check_classifier_not_supporting_multiclass(*args, **kwargs):' >> sklearn/utils/estimator_checks.py && \
    echo '    """Stub: check_classifier_not_supporting_multiclass not available in this milestone"""' >> sklearn/utils/estimator_checks.py && \
    echo '    raise NotImplementedError("check_classifier_not_supporting_multiclass not available in M12.1")' >> sklearn/utils/estimator_checks.py

# 6. Add assert_docstring_consistency to sklearn/utils/_testing.py
RUN cd /testbed && \
    echo '' >> sklearn/utils/_testing.py && \
    echo '# [ENV-PATCH] Stub for assert_docstring_consistency - API not in M12.1' >> sklearn/utils/_testing.py && \
    echo 'def assert_docstring_consistency(*args, **kwargs):' >> sklearn/utils/_testing.py && \
    echo '    """Stub: assert_docstring_consistency not available in this milestone"""' >> sklearn/utils/_testing.py && \
    echo '    raise NotImplementedError("assert_docstring_consistency not available in M12.1")' >> sklearn/utils/_testing.py

# 7. Add validate_data and _check_n_features to sklearn/utils/validation.py
RUN cd /testbed && \
    echo '' >> sklearn/utils/validation.py && \
    echo '# [ENV-PATCH] Stub for validate_data - API not in M12.1' >> sklearn/utils/validation.py && \
    echo 'def validate_data(*args, **kwargs):' >> sklearn/utils/validation.py && \
    echo '    """Stub: validate_data not available in this milestone"""' >> sklearn/utils/validation.py && \
    echo '    raise NotImplementedError("validate_data not available in M12.1")' >> sklearn/utils/validation.py && \
    echo '' >> sklearn/utils/validation.py && \
    echo '# [ENV-PATCH] Stub for _check_n_features - API not in M12.1' >> sklearn/utils/validation.py && \
    echo 'def _check_n_features(*args, **kwargs):' >> sklearn/utils/validation.py && \
    echo '    """Stub: _check_n_features not available in this milestone"""' >> sklearn/utils/validation.py && \
    echo '    raise NotImplementedError("_check_n_features not available in M12.1")' >> sklearn/utils/validation.py

# 8. Add multiple stubs to sklearn/utils/estimator_checks.py
# These are needed for test_estimator_checks.py to collect
RUN cd /testbed && \
    cat >> sklearn/utils/estimator_checks.py << 'STUBEOF'

# [ENV-PATCH] Stub functions for test collection compatibility - APIs not in M12.1
class _NotAnArray:
    """Stub class for _NotAnArray - API not in M12.1"""
    pass

def check_estimator_cloneable(*args, **kwargs):
    """Stub: check_estimator_cloneable not available in this milestone"""
    raise NotImplementedError("check_estimator_cloneable not available in M12.1")

def check_estimator_repr(*args, **kwargs):
    """Stub: check_estimator_repr not available in this milestone"""
    raise NotImplementedError("check_estimator_repr not available in M12.1")

def check_estimator_tags_renamed(*args, **kwargs):
    """Stub: check_estimator_tags_renamed not available in this milestone"""
    raise NotImplementedError("check_estimator_tags_renamed not available in M12.1")
STUBEOF

# 9. Add skip_if_no_numpydoc decorator to sklearn/utils/_testing.py
RUN cd /testbed && \
    echo '' >> sklearn/utils/_testing.py && \
    echo '# [ENV-PATCH] Stub for skip_if_no_numpydoc - API not in M12.1' >> sklearn/utils/_testing.py && \
    echo 'import pytest as _pytest' >> sklearn/utils/_testing.py && \
    echo 'def skip_if_no_numpydoc(func):' >> sklearn/utils/_testing.py && \
    echo '    """Stub decorator: skip_if_no_numpydoc not available in this milestone"""' >> sklearn/utils/_testing.py && \
    echo '    return _pytest.mark.skip(reason="[ENV-PATCH] skip_if_no_numpydoc not available")(func)' >> sklearn/utils/_testing.py

# Commit patches to START state and move tag
RUN cd /testbed && \
    git add -A && \
    git commit -m "[ENV-PATCH] Add stub modules for test collection compatibility" && \
    git tag -f milestone-M12.1-start HEAD

# Clean build directory and rebuild scikit-learn for START state
RUN cd /testbed && rm -rf build/ && pip install --no-build-isolation --editable .
