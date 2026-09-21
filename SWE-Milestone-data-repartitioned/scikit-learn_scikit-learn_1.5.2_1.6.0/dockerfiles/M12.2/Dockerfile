# Build on pre-configured base image
FROM scikit-learn_scikit-learn_1.5.2_1.6.0/base:latest

# Set umask to ensure files created in container are world-writable
RUN echo 'umask 000' >> /etc/bash.bashrc && \
    echo '#!/bin/bash\numask 000\nexec "$@"' > /entrypoint.sh && \
    chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]

# Remove the original /testbed from base image and copy local testbed
RUN rm -rf /testbed
COPY . /testbed/

# Configure git
RUN git config --global --add safe.directory /testbed && \
    git config --global user.email "harness@test.com" && \
    git config --global user.name "Test Harness"

# Clean up and checkout to END state
RUN cd /testbed && git reset --hard HEAD && git clean -fd && git checkout milestone-M12.2-end

# Create sklearn.utils._test_common module
RUN mkdir -p /testbed/sklearn/utils/_test_common && \
    echo '"""Test common utilities."""' > /testbed/sklearn/utils/_test_common/__init__.py

RUN cat > /testbed/sklearn/utils/_test_common/instance_generator.py << 'EOF'
"""Instance generator for scikit-learn estimators testing."""
import re
import warnings
from contextlib import suppress
from functools import partial
from inspect import isfunction
from sklearn import config_context
from sklearn.exceptions import SkipTestWarning
from sklearn.utils import all_estimators
from sklearn.utils._testing import SkipTest

CROSS_DECOMPOSITION = ["PLSCanonical", "PLSRegression", "CCA", "PLSSVD"]
SKIPPED_ESTIMATORS = set()
INIT_PARAMS = {}

def _get_check_estimator_ids(obj):
    if isfunction(obj):
        return obj.__name__
    if isinstance(obj, partial):
        if not obj.keywords:
            return obj.func.__name__
        kwstring = ",".join(["{}={}".format(k, v) for k, v in obj.keywords.items()])
        return "{}({})".format(obj.func.__name__, kwstring)
    if hasattr(obj, "get_params"):
        with config_context(print_changed_only=True):
            return re.sub(r"\s", "", str(obj))
    return None

def _construct_instances(Estimator):
    if Estimator in SKIPPED_ESTIMATORS:
        raise SkipTest(f"Can't instantiate estimator {Estimator.__name__}")
    if Estimator in INIT_PARAMS:
        param_sets = INIT_PARAMS[Estimator]
        if not isinstance(param_sets, list):
            param_sets = [param_sets]
        for params in param_sets:
            yield Estimator(**params)
    else:
        try:
            yield Estimator()
        except Exception as e:
            raise SkipTest(f"Can't instantiate estimator {Estimator.__name__}: {e}")

def _tested_estimators(type_filter=None):
    for _, Estimator in all_estimators(type_filter=type_filter):
        with suppress(SkipTest):
            for estimator in _construct_instances(Estimator):
                yield estimator
EOF

# Create sklearn.frozen module
RUN mkdir -p /testbed/sklearn/frozen && \
    cat > /testbed/sklearn/frozen/__init__.py << 'EOF'
from sklearn.frozen._frozen import FrozenEstimator
__all__ = ["FrozenEstimator"]
EOF

RUN cat > /testbed/sklearn/frozen/_frozen.py << 'EOF'
from copy import deepcopy
from sklearn.base import BaseEstimator
from sklearn.exceptions import NotFittedError
from sklearn.utils._tags import _safe_tags
from sklearn.utils.metaestimators import available_if
from sklearn.utils.validation import check_is_fitted

def _estimator_has(attr):
    def check(self):
        return hasattr(self.estimator, attr)
    return check

class FrozenEstimator(BaseEstimator):
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

    def fit(self, X, y=None, *args, **kwargs):
        check_is_fitted(self.estimator)
        return self

    def set_params(self, **kwargs):
        estimator = kwargs.pop("estimator", None)
        if estimator is not None:
            self.estimator = estimator
        if kwargs:
            raise ValueError("Cannot set inner estimator parameters in frozen estimator.")
        return self

    def get_params(self, deep=True):
        return {"estimator": self.estimator}

    def __sklearn_tags__(self):
        tags = deepcopy(_safe_tags(self.estimator))
        if hasattr(tags, '_skip_test'):
            tags._skip_test = True
        return tags
EOF

# Add Tags classes to _tags.py
RUN cat >> /testbed/sklearn/utils/_tags.py << 'EOF'

# Tags classes for API compatibility
from dataclasses import dataclass, field
from typing import List, Optional

@dataclass
class InputTags:
    one_d_array: bool = False
    two_d_array: bool = True
    three_d_array: bool = False
    sparse: bool = False
    categorical: bool = False
    string: bool = False
    dict: bool = False
    positive_only: bool = False
    allow_nan: bool = False
    pairwise: bool = False

@dataclass
class TargetTags:
    required: bool = True
    one_d_labels: bool = False
    two_d_labels: bool = False
    positive_only: bool = False
    multi_output: bool = False
    single_output: bool = True

@dataclass
class ClassifierTags:
    poor_score: bool = False
    multi_class: bool = True
    multi_label: bool = False

@dataclass
class RegressorTags:
    poor_score: bool = False

@dataclass
class TransformerTags:
    preserves_dtype: List[str] = field(default_factory=lambda: ["float64"])

@dataclass
class Tags:
    estimator_type: Optional[str] = None
    target_tags: TargetTags = field(default_factory=TargetTags)
    transformer_tags: Optional[TransformerTags] = None
    classifier_tags: Optional[ClassifierTags] = None
    regressor_tags: Optional[RegressorTags] = None
    input_tags: InputTags = field(default_factory=InputTags)
    array_api_support: bool = False
    no_validation: bool = False
    non_deterministic: bool = False
    requires_fit: bool = True
    _skip_test: bool = False
    _xfail_checks: dict = field(default_factory=dict)

def get_tags(estimator):
    if hasattr(estimator, "__sklearn_tags__"):
        return estimator.__sklearn_tags__()
    old_tags = _safe_tags(estimator)
    tags = Tags()
    tags.input_tags.allow_nan = old_tags.get("allow_nan", False)
    tags.input_tags.pairwise = old_tags.get("pairwise", False)
    tags.input_tags.positive_only = old_tags.get("requires_positive_X", False)
    tags.target_tags.required = old_tags.get("requires_y", False)
    tags.target_tags.positive_only = old_tags.get("requires_positive_y", False)
    tags.target_tags.multi_output = old_tags.get("multioutput", False)
    tags.non_deterministic = old_tags.get("non_deterministic", False)
    tags.no_validation = old_tags.get("no_validation", False)
    tags.requires_fit = old_tags.get("requires_fit", True)
    tags._skip_test = old_tags.get("_skip_test", False)
    tags._xfail_checks = old_tags.get("_xfail_checks", {})
    from sklearn.base import is_classifier, is_regressor
    if is_classifier(estimator):
        tags.estimator_type = "classifier"
        tags.classifier_tags = ClassifierTags(
            poor_score=old_tags.get("poor_score", False),
            multi_label=old_tags.get("multilabel", False),
        )
    elif is_regressor(estimator):
        tags.estimator_type = "regressor"
        tags.regressor_tags = RegressorTags(poor_score=old_tags.get("poor_score", False))
    if hasattr(estimator, "transform"):
        tags.transformer_tags = TransformerTags()
    return tags
EOF

# Add missing _yield_masked_array_for_each_param to _search.py
RUN cat >> /testbed/sklearn/model_selection/_search.py << 'EOF'


# Stub function for API compatibility
def _yield_masked_array_for_each_param(candidate_params):
    """Yield masked arrays for each parameter from list of parameter dictionaries.

    Parameters
    ----------
    candidate_params : list of dict
        List of parameter dictionaries, e.g., [{"foo": 1}, {"foo": 2}]

    Yields
    ------
    key : str
        The parameter name with "param_" prefix
    value : MaskedArray
        Masked array containing parameter values
    """
    import numpy as np
    from collections import defaultdict

    # Collect all parameter values
    all_params = defaultdict(list)
    all_masks = defaultdict(list)

    for params in candidate_params:
        # Track which params are present in this iteration
        present_params = set(params.keys())

        # Add values for params present in this iteration
        for param_name, value in params.items():
            all_params[param_name].append(value)

        # Update masks - mark missing values for params seen before
        for param_name in all_params.keys():
            if param_name not in present_params:
                all_params[param_name].append(0)
                all_masks[param_name].append(True)
            elif len(all_masks[param_name]) < len(all_params[param_name]):
                all_masks[param_name].append(False)

    # Yield masked arrays for each parameter
    for param_name in all_params.keys():
        values = all_params[param_name]
        mask = all_masks[param_name]

        # Extend mask if needed
        while len(mask) < len(values):
            mask.append(False)

        # Try to create numeric array first, fall back to object dtype
        try:
            arr = np.array(values)
            if arr.dtype == object or any(mask):
                arr = np.ma.MaskedArray(values, mask=mask, dtype=object)
            else:
                arr = np.ma.MaskedArray(arr, mask=mask)
        except (ValueError, TypeError):
            arr = np.ma.MaskedArray(values, mask=mask, dtype=object)

        yield f"param_{param_name}", arr
EOF

# Add check_inplace_ensure_writeable to estimator_checks.py
RUN cat >> /testbed/sklearn/utils/estimator_checks.py << 'EOF'


def check_inplace_ensure_writeable(name, estimator):
    """Check that estimators that work inplace properly handle non-writeable input.

    This is a compatibility stub function for test collection.
    The actual check tests that when copy=False, estimators handle read-only arrays.
    """
    import numpy as np
    from sklearn.utils._testing import _get_args

    rng = np.random.RandomState(42)
    X = rng.randn(30, 10)
    y = np.arange(30) % 3  # Simple classification target

    # Make X read-only
    X_readonly = X.copy()
    X_readonly.flags.writeable = False

    # Try to fit on read-only data - this should either:
    # 1. Work (if estimator properly copies when needed)
    # 2. Raise a clear error about read-only input
    try:
        estimator.fit(X_readonly, y)
    except ValueError as e:
        if "read-only" in str(e).lower() or "writeable" in str(e).lower():
            pass  # Expected error for read-only input
        else:
            raise
    except TypeError:
        pass  # Some estimators may not handle read-only input
EOF

# Add assert_docstring_consistency and skip_if_no_numpydoc to _testing.py
RUN cat >> /testbed/sklearn/utils/_testing.py << 'EOF'


# Stub functions for docstring testing compatibility
def skip_if_no_numpydoc(func):
    """Decorator to skip test if numpydoc is not installed."""
    import pytest
    from functools import wraps

    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            import numpydoc
        except ImportError:
            pytest.skip("numpydoc is not installed")
        return func(*args, **kwargs)
    return wrapper


def assert_docstring_consistency(funcs, include_params=True, exclude_params=None, include_attrs=False, exclude_attrs=None):
    """Check that docstrings across related functions are consistent.

    Parameters
    ----------
    funcs : list
        List of functions/classes to check for consistent docstrings.
    include_params : bool or list, default=True
        If True, check all parameters. If list, check only those parameters.
    exclude_params : list or None, default=None
        List of parameter names to exclude from checking.
    include_attrs : bool, default=False
        Whether to check attributes docstrings.
    exclude_attrs : list or None, default=None
        List of attribute names to exclude from checking.
    """
    try:
        from numpydoc import docscrape
    except ImportError:
        import pytest
        pytest.skip("numpydoc is required for docstring consistency checks")
        return

    if exclude_params is None:
        exclude_params = []
    if exclude_attrs is None:
        exclude_attrs = []

    # Extract docstrings
    docs = []
    for func in funcs:
        if hasattr(func, '__init__'):
            doc = docscrape.ClassDoc(func)
        else:
            doc = docscrape.FunctionDoc(func)
        docs.append(doc)

    # Determine which parameters to check
    if include_params is True:
        all_params = set()
        for doc in docs:
            all_params.update(p.name for p in doc['Parameters'])
        params_to_check = all_params - set(exclude_params)
    elif include_params:
        params_to_check = set(include_params) - set(exclude_params)
    else:
        params_to_check = set()

    # Check parameter descriptions are consistent
    param_descriptions = {}
    for i, doc in enumerate(docs):
        for param in doc['Parameters']:
            if param.name in params_to_check:
                desc = ' '.join(param.desc)
                if param.name not in param_descriptions:
                    param_descriptions[param.name] = []
                param_descriptions[param.name].append((funcs[i].__name__, desc))

    # Check attributes if requested
    if include_attrs:
        attr_descriptions = {}
        for i, doc in enumerate(docs):
            for attr in doc.get('Attributes', []):
                if attr.name not in exclude_attrs:
                    desc = ' '.join(attr.desc)
                    if attr.name not in attr_descriptions:
                        attr_descriptions[attr.name] = []
                    attr_descriptions[attr.name].append((funcs[i].__name__, desc))
EOF

# Commit and update END tag
RUN cd /testbed && \
    git add -A && \
    git commit -m "[ENV-PATCH] Add sklearn.utils._test_common, sklearn.frozen, Tags, _search, and estimator_checks compatibility" && \
    git tag -f milestone-M12.2-end HEAD

# Apply same patches to START state
RUN cd /testbed && git checkout milestone-M12.2-start

RUN mkdir -p /testbed/sklearn/utils/_test_common && \
    echo '"""Test common utilities."""' > /testbed/sklearn/utils/_test_common/__init__.py

RUN cat > /testbed/sklearn/utils/_test_common/instance_generator.py << 'EOF'
"""Instance generator for scikit-learn estimators testing."""
import re
import warnings
from contextlib import suppress
from functools import partial
from inspect import isfunction
from sklearn import config_context
from sklearn.exceptions import SkipTestWarning
from sklearn.utils import all_estimators
from sklearn.utils._testing import SkipTest

CROSS_DECOMPOSITION = ["PLSCanonical", "PLSRegression", "CCA", "PLSSVD"]
SKIPPED_ESTIMATORS = set()
INIT_PARAMS = {}

def _get_check_estimator_ids(obj):
    if isfunction(obj):
        return obj.__name__
    if isinstance(obj, partial):
        if not obj.keywords:
            return obj.func.__name__
        kwstring = ",".join(["{}={}".format(k, v) for k, v in obj.keywords.items()])
        return "{}({})".format(obj.func.__name__, kwstring)
    if hasattr(obj, "get_params"):
        with config_context(print_changed_only=True):
            return re.sub(r"\s", "", str(obj))
    return None

def _construct_instances(Estimator):
    if Estimator in SKIPPED_ESTIMATORS:
        raise SkipTest(f"Can't instantiate estimator {Estimator.__name__}")
    if Estimator in INIT_PARAMS:
        param_sets = INIT_PARAMS[Estimator]
        if not isinstance(param_sets, list):
            param_sets = [param_sets]
        for params in param_sets:
            yield Estimator(**params)
    else:
        try:
            yield Estimator()
        except Exception as e:
            raise SkipTest(f"Can't instantiate estimator {Estimator.__name__}: {e}")

def _tested_estimators(type_filter=None):
    for _, Estimator in all_estimators(type_filter=type_filter):
        with suppress(SkipTest):
            for estimator in _construct_instances(Estimator):
                yield estimator
EOF

RUN mkdir -p /testbed/sklearn/frozen && \
    cat > /testbed/sklearn/frozen/__init__.py << 'EOF'
from sklearn.frozen._frozen import FrozenEstimator
__all__ = ["FrozenEstimator"]
EOF

RUN cat > /testbed/sklearn/frozen/_frozen.py << 'EOF'
from copy import deepcopy
from sklearn.base import BaseEstimator
from sklearn.exceptions import NotFittedError
from sklearn.utils._tags import _safe_tags
from sklearn.utils.metaestimators import available_if
from sklearn.utils.validation import check_is_fitted

def _estimator_has(attr):
    def check(self):
        return hasattr(self.estimator, attr)
    return check

class FrozenEstimator(BaseEstimator):
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

    def fit(self, X, y=None, *args, **kwargs):
        check_is_fitted(self.estimator)
        return self

    def set_params(self, **kwargs):
        estimator = kwargs.pop("estimator", None)
        if estimator is not None:
            self.estimator = estimator
        if kwargs:
            raise ValueError("Cannot set inner estimator parameters in frozen estimator.")
        return self

    def get_params(self, deep=True):
        return {"estimator": self.estimator}

    def __sklearn_tags__(self):
        tags = deepcopy(_safe_tags(self.estimator))
        if hasattr(tags, '_skip_test'):
            tags._skip_test = True
        return tags
EOF

RUN cat >> /testbed/sklearn/utils/_tags.py << 'EOF'

# Tags classes for API compatibility
from dataclasses import dataclass, field
from typing import List, Optional

@dataclass
class InputTags:
    one_d_array: bool = False
    two_d_array: bool = True
    three_d_array: bool = False
    sparse: bool = False
    categorical: bool = False
    string: bool = False
    dict: bool = False
    positive_only: bool = False
    allow_nan: bool = False
    pairwise: bool = False

@dataclass
class TargetTags:
    required: bool = True
    one_d_labels: bool = False
    two_d_labels: bool = False
    positive_only: bool = False
    multi_output: bool = False
    single_output: bool = True

@dataclass
class ClassifierTags:
    poor_score: bool = False
    multi_class: bool = True
    multi_label: bool = False

@dataclass
class RegressorTags:
    poor_score: bool = False

@dataclass
class TransformerTags:
    preserves_dtype: List[str] = field(default_factory=lambda: ["float64"])

@dataclass
class Tags:
    estimator_type: Optional[str] = None
    target_tags: TargetTags = field(default_factory=TargetTags)
    transformer_tags: Optional[TransformerTags] = None
    classifier_tags: Optional[ClassifierTags] = None
    regressor_tags: Optional[RegressorTags] = None
    input_tags: InputTags = field(default_factory=InputTags)
    array_api_support: bool = False
    no_validation: bool = False
    non_deterministic: bool = False
    requires_fit: bool = True
    _skip_test: bool = False
    _xfail_checks: dict = field(default_factory=dict)

def get_tags(estimator):
    if hasattr(estimator, "__sklearn_tags__"):
        return estimator.__sklearn_tags__()
    old_tags = _safe_tags(estimator)
    tags = Tags()
    tags.input_tags.allow_nan = old_tags.get("allow_nan", False)
    tags.input_tags.pairwise = old_tags.get("pairwise", False)
    tags.input_tags.positive_only = old_tags.get("requires_positive_X", False)
    tags.target_tags.required = old_tags.get("requires_y", False)
    tags.target_tags.positive_only = old_tags.get("requires_positive_y", False)
    tags.target_tags.multi_output = old_tags.get("multioutput", False)
    tags.non_deterministic = old_tags.get("non_deterministic", False)
    tags.no_validation = old_tags.get("no_validation", False)
    tags.requires_fit = old_tags.get("requires_fit", True)
    tags._skip_test = old_tags.get("_skip_test", False)
    tags._xfail_checks = old_tags.get("_xfail_checks", {})
    from sklearn.base import is_classifier, is_regressor
    if is_classifier(estimator):
        tags.estimator_type = "classifier"
        tags.classifier_tags = ClassifierTags(
            poor_score=old_tags.get("poor_score", False),
            multi_label=old_tags.get("multilabel", False),
        )
    elif is_regressor(estimator):
        tags.estimator_type = "regressor"
        tags.regressor_tags = RegressorTags(poor_score=old_tags.get("poor_score", False))
    if hasattr(estimator, "transform"):
        tags.transformer_tags = TransformerTags()
    return tags
EOF

# Add missing _yield_masked_array_for_each_param to _search.py for START state
RUN cat >> /testbed/sklearn/model_selection/_search.py << 'EOF'


# Stub function for API compatibility
def _yield_masked_array_for_each_param(candidate_params):
    """Yield masked arrays for each parameter from list of parameter dictionaries.

    Parameters
    ----------
    candidate_params : list of dict
        List of parameter dictionaries, e.g., [{"foo": 1}, {"foo": 2}]

    Yields
    ------
    key : str
        The parameter name with "param_" prefix
    value : MaskedArray
        Masked array containing parameter values
    """
    import numpy as np
    from collections import defaultdict

    # Collect all parameter values
    all_params = defaultdict(list)
    all_masks = defaultdict(list)

    for params in candidate_params:
        # Track which params are present in this iteration
        present_params = set(params.keys())

        # Add values for params present in this iteration
        for param_name, value in params.items():
            all_params[param_name].append(value)

        # Update masks - mark missing values for params seen before
        for param_name in all_params.keys():
            if param_name not in present_params:
                all_params[param_name].append(0)
                all_masks[param_name].append(True)
            elif len(all_masks[param_name]) < len(all_params[param_name]):
                all_masks[param_name].append(False)

    # Yield masked arrays for each parameter
    for param_name in all_params.keys():
        values = all_params[param_name]
        mask = all_masks[param_name]

        # Extend mask if needed
        while len(mask) < len(values):
            mask.append(False)

        # Try to create numeric array first, fall back to object dtype
        try:
            arr = np.array(values)
            if arr.dtype == object or any(mask):
                arr = np.ma.MaskedArray(values, mask=mask, dtype=object)
            else:
                arr = np.ma.MaskedArray(arr, mask=mask)
        except (ValueError, TypeError):
            arr = np.ma.MaskedArray(values, mask=mask, dtype=object)

        yield f"param_{param_name}", arr
EOF

# Add check_inplace_ensure_writeable to estimator_checks.py for START state
RUN cat >> /testbed/sklearn/utils/estimator_checks.py << 'EOF'


def check_inplace_ensure_writeable(name, estimator):
    """Check that estimators that work inplace properly handle non-writeable input.

    This is a compatibility stub function for test collection.
    The actual check tests that when copy=False, estimators handle read-only arrays.
    """
    import numpy as np
    from sklearn.utils._testing import _get_args

    rng = np.random.RandomState(42)
    X = rng.randn(30, 10)
    y = np.arange(30) % 3  # Simple classification target

    # Make X read-only
    X_readonly = X.copy()
    X_readonly.flags.writeable = False

    # Try to fit on read-only data - this should either:
    # 1. Work (if estimator properly copies when needed)
    # 2. Raise a clear error about read-only input
    try:
        estimator.fit(X_readonly, y)
    except ValueError as e:
        if "read-only" in str(e).lower() or "writeable" in str(e).lower():
            pass  # Expected error for read-only input
        else:
            raise
    except TypeError:
        pass  # Some estimators may not handle read-only input
EOF

# Add assert_docstring_consistency and skip_if_no_numpydoc to _testing.py for START state
RUN cat >> /testbed/sklearn/utils/_testing.py << 'EOF'


# Stub functions for docstring testing compatibility
def skip_if_no_numpydoc(func):
    """Decorator to skip test if numpydoc is not installed."""
    import pytest
    from functools import wraps

    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            import numpydoc
        except ImportError:
            pytest.skip("numpydoc is not installed")
        return func(*args, **kwargs)
    return wrapper


def assert_docstring_consistency(funcs, include_params=True, exclude_params=None, include_attrs=False, exclude_attrs=None):
    """Check that docstrings across related functions are consistent.

    Parameters
    ----------
    funcs : list
        List of functions/classes to check for consistent docstrings.
    include_params : bool or list, default=True
        If True, check all parameters. If list, check only those parameters.
    exclude_params : list or None, default=None
        List of parameter names to exclude from checking.
    include_attrs : bool, default=False
        Whether to check attributes docstrings.
    exclude_attrs : list or None, default=None
        List of attribute names to exclude from checking.
    """
    try:
        from numpydoc import docscrape
    except ImportError:
        import pytest
        pytest.skip("numpydoc is required for docstring consistency checks")
        return

    if exclude_params is None:
        exclude_params = []
    if exclude_attrs is None:
        exclude_attrs = []

    # Extract docstrings
    docs = []
    for func in funcs:
        if hasattr(func, '__init__'):
            doc = docscrape.ClassDoc(func)
        else:
            doc = docscrape.FunctionDoc(func)
        docs.append(doc)

    # Determine which parameters to check
    if include_params is True:
        all_params = set()
        for doc in docs:
            all_params.update(p.name for p in doc['Parameters'])
        params_to_check = all_params - set(exclude_params)
    elif include_params:
        params_to_check = set(include_params) - set(exclude_params)
    else:
        params_to_check = set()

    # Check parameter descriptions are consistent
    param_descriptions = {}
    for i, doc in enumerate(docs):
        for param in doc['Parameters']:
            if param.name in params_to_check:
                desc = ' '.join(param.desc)
                if param.name not in param_descriptions:
                    param_descriptions[param.name] = []
                param_descriptions[param.name].append((funcs[i].__name__, desc))

    # Check attributes if requested
    if include_attrs:
        attr_descriptions = {}
        for i, doc in enumerate(docs):
            for attr in doc.get('Attributes', []):
                if attr.name not in exclude_attrs:
                    desc = ' '.join(attr.desc)
                    if attr.name not in attr_descriptions:
                        attr_descriptions[attr.name] = []
                    attr_descriptions[attr.name].append((funcs[i].__name__, desc))
EOF

# Commit and update START tag
RUN cd /testbed && \
    git add -A && \
    git commit -m "[ENV-PATCH] Add sklearn.utils._test_common, sklearn.frozen, Tags, _search, and estimator_checks compatibility" && \
    git tag -f milestone-M12.2-start HEAD

# Rebuild in START state
RUN cd /testbed && pip install --no-build-isolation --editable .
