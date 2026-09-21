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

# Ensure git considers testbed safe and configure git identity for commits
RUN git config --global --add safe.directory /testbed && \
    git config --global user.email "env-patch@scikit-learn.org" && \
    git config --global user.name "Environment Patch"

# ============================================================================
# APPLY PATCHES TO END STATE FIRST
# ============================================================================
RUN cd /testbed && git checkout milestone-M12.3-end

# [ENV-PATCH] Add FrozenEstimator module that is imported by calibration.py but
# not present in the milestone state (due to incomplete cherry-pick)
RUN echo '# Authors: The scikit-learn developers' > /testbed/sklearn/frozen/__init__.py && \
    echo '# SPDX-License-Identifier: BSD-3-Clause' >> /testbed/sklearn/frozen/__init__.py && \
    echo '# [ENV-PATCH] Added for milestone M12.3 compatibility' >> /testbed/sklearn/frozen/__init__.py && \
    echo '' >> /testbed/sklearn/frozen/__init__.py && \
    echo 'from ._frozen import FrozenEstimator' >> /testbed/sklearn/frozen/__init__.py && \
    echo '' >> /testbed/sklearn/frozen/__init__.py && \
    echo '__all__ = ["FrozenEstimator"]' >> /testbed/sklearn/frozen/__init__.py

# Create sklearn/frozen/_frozen.py with simplified FrozenEstimator
RUN cat > /testbed/sklearn/frozen/_frozen.py << 'FROZEN_EOF'
# Authors: The scikit-learn developers
# SPDX-License-Identifier: BSD-3-Clause
# [ENV-PATCH] Simplified FrozenEstimator for milestone M12.3 compatibility

from copy import deepcopy

from ..base import BaseEstimator
from ..exceptions import NotFittedError
from ..utils._tags import _safe_tags
from ..utils.metaestimators import available_if
from ..utils.validation import check_is_fitted


def _estimator_has(attr):
    def check(self):
        getattr(self.estimator, attr)
        return True
    return check


class FrozenEstimator(BaseEstimator):
    """Estimator that wraps a fitted estimator to prevent re-fitting."""

    def __init__(self, estimator):
        self.estimator = estimator

    @available_if(_estimator_has("__getitem__"))
    def __getitem__(self, *args, **kwargs):
        return self.estimator.__getitem__(*args, **kwargs)

    def __getattr__(self, name):
        if name in ["fit_predict", "fit_transform"]:
            raise AttributeError(f"{name} is not available for frozen estimators.")
        return getattr(self.estimator, name)

    def __sklearn_clone__(self):
        return self

    def __sklearn_is_fitted__(self):
        try:
            check_is_fitted(self.estimator)
            return True
        except NotFittedError:
            return False

    def fit(self, X, y, *args, **kwargs):
        check_is_fitted(self.estimator)
        return self

    def set_params(self, **kwargs):
        estimator = kwargs.pop("estimator", None)
        if estimator is not None:
            self.estimator = estimator
        if kwargs:
            raise ValueError(
                "You cannot set parameters of the inner estimator in a frozen "
                "estimator since calling `fit` has no effect."
            )

    def get_params(self, deep=True):
        return {"estimator": self.estimator}

    def _get_tags(self):
        tags = deepcopy(_safe_tags(self.estimator))
        tags["_skip_test"] = True
        return tags

    def _more_tags(self):
        return {"_skip_test": True}
FROZEN_EOF

# [ENV-PATCH] Add get_tags function to sklearn.utils._tags for test_common.py
RUN cat >> /testbed/sklearn/utils/_tags.py << 'TAGS_EOF'

# [ENV-PATCH] Stub function for milestone M12.3 compatibility
def get_tags(estimator):
    """Stub for get_tags - returns tags from _safe_tags."""
    return _safe_tags(estimator)
TAGS_EOF

# [ENV-PATCH] Add sklearn.utils._test_common module stub for test imports
RUN mkdir -p /testbed/sklearn/utils/_test_common && \
    cat > /testbed/sklearn/utils/_test_common/__init__.py << 'TESTCOMMON_EOF'
# [ENV-PATCH] Stub module for milestone M12.3 compatibility
TESTCOMMON_EOF

# [ENV-PATCH] Add _get_check_estimator_ids and _tested_estimators to instance_generator
RUN cat > /testbed/sklearn/utils/_test_common/instance_generator.py << 'INSTGEN_EOF'
# [ENV-PATCH] Stub module for milestone M12.3 compatibility
from functools import partial

def _get_check_estimator_ids(obj):
    """Get a descriptive ID for an estimator or callable."""
    if hasattr(obj, '__class__'):
        if hasattr(obj, 'get_params'):
            # For estimators, include non-default params
            try:
                params = obj.get_params(deep=False)
                # Filter out default params
                param_str = ','.join(f'{k}={v!r}' for k, v in sorted(params.items())
                                     if v is not None and v != 1.0 and v != True)
                if param_str:
                    return f"{obj.__class__.__name__}({param_str})"
                return obj.__class__.__name__ + "()"
            except Exception:
                return obj.__class__.__name__ + "()"
        return obj.__class__.__name__
    if callable(obj):
        if isinstance(obj, partial):
            if obj.keywords:
                return f"{obj.func.__name__}({','.join(f'{k}={v}' for k, v in obj.keywords.items())})"
            return obj.func.__name__
        return getattr(obj, '__name__', str(obj))
    return str(obj)


def _tested_estimators(type_filter=None):
    """Yield all estimators to test.

    This is a stub that yields a minimal set of estimators for testing.
    The actual implementation would use all_estimators() and filter them.

    Parameters
    ----------
    type_filter : str or None
        Filter to use for selecting estimators. Options: "classifier",
        "regressor", "transformer", "cluster", None (no filter).
    """
    from sklearn.linear_model import LogisticRegression, LinearRegression
    from sklearn.tree import DecisionTreeClassifier, DecisionTreeRegressor
    from sklearn.preprocessing import StandardScaler
    from sklearn.cluster import KMeans

    if type_filter == "classifier":
        yield LogisticRegression()
        yield DecisionTreeClassifier()
    elif type_filter == "regressor":
        yield LinearRegression()
        yield DecisionTreeRegressor()
    elif type_filter == "transformer":
        yield StandardScaler()
    elif type_filter == "cluster":
        yield KMeans(n_clusters=2)
    else:
        # No filter - yield all
        yield LogisticRegression()
        yield DecisionTreeClassifier()
        yield StandardScaler()
INSTGEN_EOF

# [ENV-PATCH] Add _get_threadpool_controller to sklearn.utils.parallel
RUN cat >> /testbed/sklearn/utils/parallel.py << 'PARALLEL_EOF'

# [ENV-PATCH] Stub function for milestone M12.3 compatibility
def _get_threadpool_controller():
    """Stub function that returns None (not fully implemented)."""
    try:
        from threadpoolctl import ThreadpoolController
        return ThreadpoolController()
    except ImportError:
        return None
PARALLEL_EOF

# [ENV-PATCH] Add validate_data stub to sklearn.utils.validation
RUN cat >> /testbed/sklearn/utils/validation.py << 'VALIDATE_EOF'

# [ENV-PATCH] Stub function for milestone M12.3 compatibility
def validate_data(
    _estimator,
    X="no_validation",
    y="no_validation",
    *,
    reset=True,
    validate_separately=False,
    skip_check_array=False,
    **check_params,
):
    """Stub for validate_data - delegates to check_X_y or check_array."""
    if y is None or y == "no_validation":
        return check_array(X, **check_params)
    else:
        return check_X_y(X, y, **check_params)
VALIDATE_EOF

# [ENV-PATCH] Add _yield_masked_array_for_each_param stub to sklearn.model_selection._search
RUN cat >> /testbed/sklearn/model_selection/_search.py << 'SEARCH_EOF'

# [ENV-PATCH] Stub function for milestone M12.3 compatibility
def _yield_masked_array_for_each_param(masked_array):
    """Stub function that yields items from masked array."""
    if hasattr(masked_array, '__iter__'):
        yield from masked_array
SEARCH_EOF

# [ENV-PATCH] Add check_inplace_ensure_writeable stub to sklearn.utils.estimator_checks
RUN cat >> /testbed/sklearn/utils/estimator_checks.py << 'ESTIMATOR_EOF'

# [ENV-PATCH] Stub function for milestone M12.3 compatibility
def check_inplace_ensure_writeable(name, estimator_orig):
    """Stub function for check_inplace_ensure_writeable."""
    pass
ESTIMATOR_EOF

# Commit patches and update END tag
RUN cd /testbed && \
    git add -A && \
    git commit -m "[ENV-PATCH] Add stub modules for M12.3 END state compatibility" && \
    git tag -f milestone-M12.3-end HEAD

# ============================================================================
# APPLY SAME PATCHES TO START STATE
# ============================================================================
RUN cd /testbed && git checkout milestone-M12.3-start

# [ENV-PATCH] Add FrozenEstimator module for START state
RUN echo '# Authors: The scikit-learn developers' > /testbed/sklearn/frozen/__init__.py && \
    echo '# SPDX-License-Identifier: BSD-3-Clause' >> /testbed/sklearn/frozen/__init__.py && \
    echo '# [ENV-PATCH] Added for milestone M12.3 compatibility' >> /testbed/sklearn/frozen/__init__.py && \
    echo '' >> /testbed/sklearn/frozen/__init__.py && \
    echo 'from ._frozen import FrozenEstimator' >> /testbed/sklearn/frozen/__init__.py && \
    echo '' >> /testbed/sklearn/frozen/__init__.py && \
    echo '__all__ = ["FrozenEstimator"]' >> /testbed/sklearn/frozen/__init__.py

RUN cat > /testbed/sklearn/frozen/_frozen.py << 'FROZEN_EOF'
# Authors: The scikit-learn developers
# SPDX-License-Identifier: BSD-3-Clause
# [ENV-PATCH] Simplified FrozenEstimator for milestone M12.3 compatibility

from copy import deepcopy

from ..base import BaseEstimator
from ..exceptions import NotFittedError
from ..utils._tags import _safe_tags
from ..utils.metaestimators import available_if
from ..utils.validation import check_is_fitted


def _estimator_has(attr):
    def check(self):
        getattr(self.estimator, attr)
        return True
    return check


class FrozenEstimator(BaseEstimator):
    """Estimator that wraps a fitted estimator to prevent re-fitting."""

    def __init__(self, estimator):
        self.estimator = estimator

    @available_if(_estimator_has("__getitem__"))
    def __getitem__(self, *args, **kwargs):
        return self.estimator.__getitem__(*args, **kwargs)

    def __getattr__(self, name):
        if name in ["fit_predict", "fit_transform"]:
            raise AttributeError(f"{name} is not available for frozen estimators.")
        return getattr(self.estimator, name)

    def __sklearn_clone__(self):
        return self

    def __sklearn_is_fitted__(self):
        try:
            check_is_fitted(self.estimator)
            return True
        except NotFittedError:
            return False

    def fit(self, X, y, *args, **kwargs):
        check_is_fitted(self.estimator)
        return self

    def set_params(self, **kwargs):
        estimator = kwargs.pop("estimator", None)
        if estimator is not None:
            self.estimator = estimator
        if kwargs:
            raise ValueError(
                "You cannot set parameters of the inner estimator in a frozen "
                "estimator since calling `fit` has no effect."
            )

    def get_params(self, deep=True):
        return {"estimator": self.estimator}

    def _get_tags(self):
        tags = deepcopy(_safe_tags(self.estimator))
        tags["_skip_test"] = True
        return tags

    def _more_tags(self):
        return {"_skip_test": True}
FROZEN_EOF

# [ENV-PATCH] Add get_tags function to sklearn.utils._tags for START state
RUN cat >> /testbed/sklearn/utils/_tags.py << 'TAGS_EOF'

# [ENV-PATCH] Stub function for milestone M12.3 compatibility
def get_tags(estimator):
    """Stub for get_tags - returns tags from _safe_tags."""
    return _safe_tags(estimator)
TAGS_EOF

# [ENV-PATCH] Add sklearn.utils._test_common module stub for START state
RUN mkdir -p /testbed/sklearn/utils/_test_common && \
    cat > /testbed/sklearn/utils/_test_common/__init__.py << 'TESTCOMMON_EOF'
# [ENV-PATCH] Stub module for milestone M12.3 compatibility
TESTCOMMON_EOF

# [ENV-PATCH] Add _get_check_estimator_ids and _tested_estimators for START state
RUN cat > /testbed/sklearn/utils/_test_common/instance_generator.py << 'INSTGEN_EOF'
# [ENV-PATCH] Stub module for milestone M12.3 compatibility
from functools import partial

def _get_check_estimator_ids(obj):
    """Get a descriptive ID for an estimator or callable."""
    if hasattr(obj, '__class__'):
        if hasattr(obj, 'get_params'):
            # For estimators, include non-default params
            try:
                params = obj.get_params(deep=False)
                # Filter out default params
                param_str = ','.join(f'{k}={v!r}' for k, v in sorted(params.items())
                                     if v is not None and v != 1.0 and v != True)
                if param_str:
                    return f"{obj.__class__.__name__}({param_str})"
                return obj.__class__.__name__ + "()"
            except Exception:
                return obj.__class__.__name__ + "()"
        return obj.__class__.__name__
    if callable(obj):
        if isinstance(obj, partial):
            if obj.keywords:
                return f"{obj.func.__name__}({','.join(f'{k}={v}' for k, v in obj.keywords.items())})"
            return obj.func.__name__
        return getattr(obj, '__name__', str(obj))
    return str(obj)


def _tested_estimators(type_filter=None):
    """Yield all estimators to test.

    This is a stub that yields a minimal set of estimators for testing.
    The actual implementation would use all_estimators() and filter them.

    Parameters
    ----------
    type_filter : str or None
        Filter to use for selecting estimators. Options: "classifier",
        "regressor", "transformer", "cluster", None (no filter).
    """
    from sklearn.linear_model import LogisticRegression, LinearRegression
    from sklearn.tree import DecisionTreeClassifier, DecisionTreeRegressor
    from sklearn.preprocessing import StandardScaler
    from sklearn.cluster import KMeans

    if type_filter == "classifier":
        yield LogisticRegression()
        yield DecisionTreeClassifier()
    elif type_filter == "regressor":
        yield LinearRegression()
        yield DecisionTreeRegressor()
    elif type_filter == "transformer":
        yield StandardScaler()
    elif type_filter == "cluster":
        yield KMeans(n_clusters=2)
    else:
        # No filter - yield all
        yield LogisticRegression()
        yield DecisionTreeClassifier()
        yield StandardScaler()
INSTGEN_EOF

# [ENV-PATCH] Add _get_threadpool_controller to sklearn.utils.parallel for START state
RUN cat >> /testbed/sklearn/utils/parallel.py << 'PARALLEL_EOF'

# [ENV-PATCH] Stub function for milestone M12.3 compatibility
def _get_threadpool_controller():
    """Stub function that returns None (not fully implemented)."""
    try:
        from threadpoolctl import ThreadpoolController
        return ThreadpoolController()
    except ImportError:
        return None
PARALLEL_EOF

# [ENV-PATCH] Add validate_data stub to sklearn.utils.validation for START state
RUN cat >> /testbed/sklearn/utils/validation.py << 'VALIDATE_EOF'

# [ENV-PATCH] Stub function for milestone M12.3 compatibility
def validate_data(
    _estimator,
    X="no_validation",
    y="no_validation",
    *,
    reset=True,
    validate_separately=False,
    skip_check_array=False,
    **check_params,
):
    """Stub for validate_data - delegates to check_X_y or check_array."""
    if y is None or y == "no_validation":
        return check_array(X, **check_params)
    else:
        return check_X_y(X, y, **check_params)
VALIDATE_EOF

# [ENV-PATCH] Add _yield_masked_array_for_each_param stub for START state
RUN cat >> /testbed/sklearn/model_selection/_search.py << 'SEARCH_EOF'

# [ENV-PATCH] Stub function for milestone M12.3 compatibility
def _yield_masked_array_for_each_param(masked_array):
    """Stub function that yields items from masked array."""
    if hasattr(masked_array, '__iter__'):
        yield from masked_array
SEARCH_EOF

# [ENV-PATCH] Add check_inplace_ensure_writeable stub for START state
RUN cat >> /testbed/sklearn/utils/estimator_checks.py << 'ESTIMATOR_EOF'

# [ENV-PATCH] Stub function for milestone M12.3 compatibility
def check_inplace_ensure_writeable(name, estimator_orig):
    """Stub function for check_inplace_ensure_writeable."""
    pass
ESTIMATOR_EOF

# [ENV-PATCH] Add DEPRECATED_METRICS constant to sklearn.metrics._dist_metrics for START state
# This constant is defined in a compiled Cython extension, so we need to inject it at import time
RUN cat >> /testbed/sklearn/metrics/__init__.py << 'METRICS_EOF'

# [ENV-PATCH] Inject DEPRECATED_METRICS into _dist_metrics for M12.3 START state compatibility
try:
    from sklearn.metrics import _dist_metrics
    if not hasattr(_dist_metrics, 'DEPRECATED_METRICS'):
        _dist_metrics.DEPRECATED_METRICS = frozenset(["sokalmichener", "kulsinski"])
except ImportError:
    pass
METRICS_EOF

# Commit patches and update START tag
RUN cd /testbed && \
    git add -A && \
    git commit -m "[ENV-PATCH] Add stub modules for M12.3 START state compatibility" && \
    git tag -f milestone-M12.3-start HEAD

# ============================================================================
# FINALIZE
# ============================================================================
# Set default state to START
RUN cd /testbed && git checkout milestone-M12.3-start

# Rebuild scikit-learn for START state
RUN cd /testbed && pip install --no-build-isolation --editable .
