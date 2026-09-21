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

# Set working directory
WORKDIR /testbed

# Configure git identity for commits
RUN git config --global user.email "docker@localhost" && \
    git config --global user.name "Docker Build"

# Checkout to END state (all features available)
# Tags already exist in testbed - just checkout, don't create tags
RUN cd /testbed && git checkout milestone-M12.4-end

# [ENV-PATCH] Create missing sklearn/frozen module files
# The milestone includes calibration.py that imports from sklearn.frozen
# but the frozen/__init__.py and frozen/_frozen.py files are missing
# Note: This implementation uses _safe_tags (old tag system) instead of get_tags (new tag system)
RUN mkdir -p /testbed/sklearn/frozen && \
    echo '# Authors: The scikit-learn developers' > /testbed/sklearn/frozen/__init__.py && \
    echo '# SPDX-License-Identifier: BSD-3-Clause' >> /testbed/sklearn/frozen/__init__.py && \
    echo '' >> /testbed/sklearn/frozen/__init__.py && \
    echo 'from ._frozen import FrozenEstimator' >> /testbed/sklearn/frozen/__init__.py && \
    echo '' >> /testbed/sklearn/frozen/__init__.py && \
    echo '__all__ = ["FrozenEstimator"]' >> /testbed/sklearn/frozen/__init__.py

# Create the _frozen.py file with FrozenEstimator implementation (using old tag system)
RUN cat > /testbed/sklearn/frozen/_frozen.py << 'FROZEN_EOF'
# Authors: The scikit-learn developers
# SPDX-License-Identifier: BSD-3-Clause

from copy import deepcopy

from ..base import BaseEstimator
from ..exceptions import NotFittedError
from ..utils._tags import _safe_tags
from ..utils.metaestimators import available_if
from ..utils.validation import check_is_fitted


def _estimator_has(attr):
    """Check that final_estimator has `attr`.

    Used together with `available_if`.
    """

    def check(self):
        # raise original `AttributeError` if `attr` does not exist
        getattr(self.estimator, attr)
        return True

    return check


class FrozenEstimator(BaseEstimator):
    """Estimator that wraps a fitted estimator to prevent re-fitting.

    This meta-estimator takes an estimator and freezes it, in the sense that calling
    `fit` on it has no effect. `fit_predict` and `fit_transform` are also disabled.
    All other methods are delegated to the original estimator and original estimator's
    attributes are accessible as well.

    This is particularly useful when you have a fitted or a pre-trained model as a
    transformer in a pipeline, and you'd like `pipeline.fit` to have no effect on this
    step.

    Parameters
    ----------
    estimator : estimator
        The estimator which is to be kept frozen.

    See Also
    --------
    None: No similar entry in the scikit-learn documentation.

    Examples
    --------
    >>> from sklearn.datasets import make_classification
    >>> from sklearn.frozen import FrozenEstimator
    >>> from sklearn.linear_model import LogisticRegression
    >>> X, y = make_classification(random_state=0)
    >>> clf = LogisticRegression(random_state=0).fit(X, y)
    >>> frozen_clf = FrozenEstimator(clf)
    >>> frozen_clf.fit(X, y)  # No-op
    FrozenEstimator(estimator=LogisticRegression(random_state=0))
    >>> frozen_clf.predict(X)  # Predictions from `clf.predict`
    array(...)
    """

    def __init__(self, estimator):
        self.estimator = estimator

    @available_if(_estimator_has("__getitem__"))
    def __getitem__(self, *args, **kwargs):
        """__getitem__ is defined in :class:`~sklearn.pipeline.Pipeline` and \
            :class:`~sklearn.compose.ColumnTransformer`.
        """
        return self.estimator.__getitem__(*args, **kwargs)

    def __getattr__(self, name):
        # `estimator`'s attributes are now accessible except `fit_predict` and
        # `fit_transform`
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
        """No-op.

        As a frozen estimator, calling `fit` has no effect.

        Parameters
        ----------
        X : object
            Ignored.

        y : object
            Ignored.

        *args : tuple
            Additional positional arguments. Ignored, but present for API compatibility
            with `self.estimator`.

        **kwargs : dict
            Additional keyword arguments. Ignored, but present for API compatibility
            with `self.estimator`.

        Returns
        -------
        self : object
            Returns the instance itself.
        """
        check_is_fitted(self.estimator)
        return self

    def set_params(self, **kwargs):
        """Set the parameters of this estimator.

        The only valid key here is `estimator`. You cannot set the parameters of the
        inner estimator.

        Parameters
        ----------
        **kwargs : dict
            Estimator parameters.

        Returns
        -------
        self : FrozenEstimator
            This estimator.
        """
        estimator = kwargs.pop("estimator", None)
        if estimator is not None:
            self.estimator = estimator
        if kwargs:
            raise ValueError(
                "You cannot set parameters of the inner estimator in a frozen "
                "estimator since calling `fit` has no effect. You can use "
                "`frozenestimator.estimator.set_params` to set parameters of the inner "
                "estimator."
            )

    def get_params(self, deep=True):
        """Get parameters for this estimator.

        Returns a `{"estimator": estimator}` dict. The parameters of the inner
        estimator are not included.

        Parameters
        ----------
        deep : bool, default=True
            Ignored.

        Returns
        -------
        params : dict
            Parameter names mapped to their values.
        """
        return {"estimator": self.estimator}

    def _more_tags(self):
        """Return tags for this estimator using the old tag system."""
        tags = deepcopy(_safe_tags(self.estimator))
        tags["_skip_test"] = True
        return tags
FROZEN_EOF

# [ENV-PATCH] Add missing _check_feature_names function to validation.py
# The test files import this function but it doesn't exist in this milestone state
RUN cat >> /testbed/sklearn/utils/validation.py << 'VALIDATION_PATCH_EOF'


def _check_feature_names(estimator, X, *, reset):
    """Set or check the `feature_names_in_` attribute of an estimator.

    [ENV-PATCH] Stub implementation to allow test collection.
    """
    if reset:
        feature_names_in = _get_feature_names(X)
        if feature_names_in is not None:
            estimator.feature_names_in_ = feature_names_in
        elif hasattr(estimator, "feature_names_in_"):
            delattr(estimator, "feature_names_in_")
        return

    fitted_feature_names = getattr(estimator, "feature_names_in_", None)
    X_feature_names = _get_feature_names(X)

    if fitted_feature_names is None and X_feature_names is None:
        return

    if X_feature_names is not None and fitted_feature_names is None:
        warnings.warn(
            f"X has feature names, but {estimator.__class__.__name__} was fitted "
            "without feature names"
        )
        return

    if X_feature_names is None and fitted_feature_names is not None:
        warnings.warn(
            "X does not have valid feature names, but"
            f" {estimator.__class__.__name__} was fitted with feature names"
        )
        return

    if len(fitted_feature_names) != len(X_feature_names) or np.any(
        fitted_feature_names != X_feature_names
    ):
        message = "The feature names should match those that were passed during fit.\n"
        fitted_feature_names_set = set(fitted_feature_names)
        X_feature_names_set = set(X_feature_names)
        unexpected_names = sorted(X_feature_names_set - fitted_feature_names_set)
        missing_names = sorted(fitted_feature_names_set - X_feature_names_set)
        if unexpected_names:
            message += f"Unexpected feature names: {unexpected_names}\n"
        if missing_names:
            message += f"Missing feature names: {missing_names}\n"
        raise ValueError(message)
VALIDATION_PATCH_EOF

# [ENV-PATCH] Add is_clusterer function to base.py
# The test files import this function but it doesn't exist in this milestone state
RUN cat >> /testbed/sklearn/base.py << 'BASE_PATCH_EOF'


def is_clusterer(estimator):
    """Return True if the given estimator is a clusterer.

    [ENV-PATCH] Stub implementation to allow test collection.
    """
    return getattr(estimator, "_estimator_type", None) == "clusterer"
BASE_PATCH_EOF

# Commit the patches and move the END tag to include the patches
RUN cd /testbed && \
    git add -A && \
    git commit -m "[ENV-PATCH] Add missing sklearn.frozen module and API stubs for END state" && \
    git tag -f milestone-M12.4-end HEAD

# Apply the same patch to START state
RUN cd /testbed && git checkout milestone-M12.4-start

RUN mkdir -p /testbed/sklearn/frozen && \
    echo '# Authors: The scikit-learn developers' > /testbed/sklearn/frozen/__init__.py && \
    echo '# SPDX-License-Identifier: BSD-3-Clause' >> /testbed/sklearn/frozen/__init__.py && \
    echo '' >> /testbed/sklearn/frozen/__init__.py && \
    echo 'from ._frozen import FrozenEstimator' >> /testbed/sklearn/frozen/__init__.py && \
    echo '' >> /testbed/sklearn/frozen/__init__.py && \
    echo '__all__ = ["FrozenEstimator"]' >> /testbed/sklearn/frozen/__init__.py

RUN cat > /testbed/sklearn/frozen/_frozen.py << 'FROZEN_EOF'
# Authors: The scikit-learn developers
# SPDX-License-Identifier: BSD-3-Clause

from copy import deepcopy

from ..base import BaseEstimator
from ..exceptions import NotFittedError
from ..utils._tags import _safe_tags
from ..utils.metaestimators import available_if
from ..utils.validation import check_is_fitted


def _estimator_has(attr):
    """Check that final_estimator has `attr`.

    Used together with `available_if`.
    """

    def check(self):
        # raise original `AttributeError` if `attr` does not exist
        getattr(self.estimator, attr)
        return True

    return check


class FrozenEstimator(BaseEstimator):
    """Estimator that wraps a fitted estimator to prevent re-fitting.

    This meta-estimator takes an estimator and freezes it, in the sense that calling
    `fit` on it has no effect. `fit_predict` and `fit_transform` are also disabled.
    All other methods are delegated to the original estimator and original estimator's
    attributes are accessible as well.

    This is particularly useful when you have a fitted or a pre-trained model as a
    transformer in a pipeline, and you'd like `pipeline.fit` to have no effect on this
    step.

    Parameters
    ----------
    estimator : estimator
        The estimator which is to be kept frozen.

    See Also
    --------
    None: No similar entry in the scikit-learn documentation.

    Examples
    --------
    >>> from sklearn.datasets import make_classification
    >>> from sklearn.frozen import FrozenEstimator
    >>> from sklearn.linear_model import LogisticRegression
    >>> X, y = make_classification(random_state=0)
    >>> clf = LogisticRegression(random_state=0).fit(X, y)
    >>> frozen_clf = FrozenEstimator(clf)
    >>> frozen_clf.fit(X, y)  # No-op
    FrozenEstimator(estimator=LogisticRegression(random_state=0))
    >>> frozen_clf.predict(X)  # Predictions from `clf.predict`
    array(...)
    """

    def __init__(self, estimator):
        self.estimator = estimator

    @available_if(_estimator_has("__getitem__"))
    def __getitem__(self, *args, **kwargs):
        """__getitem__ is defined in :class:`~sklearn.pipeline.Pipeline` and \
            :class:`~sklearn.compose.ColumnTransformer`.
        """
        return self.estimator.__getitem__(*args, **kwargs)

    def __getattr__(self, name):
        # `estimator`'s attributes are now accessible except `fit_predict` and
        # `fit_transform`
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
        """No-op.

        As a frozen estimator, calling `fit` has no effect.

        Parameters
        ----------
        X : object
            Ignored.

        y : object
            Ignored.

        *args : tuple
            Additional positional arguments. Ignored, but present for API compatibility
            with `self.estimator`.

        **kwargs : dict
            Additional keyword arguments. Ignored, but present for API compatibility
            with `self.estimator`.

        Returns
        -------
        self : object
            Returns the instance itself.
        """
        check_is_fitted(self.estimator)
        return self

    def set_params(self, **kwargs):
        """Set the parameters of this estimator.

        The only valid key here is `estimator`. You cannot set the parameters of the
        inner estimator.

        Parameters
        ----------
        **kwargs : dict
            Estimator parameters.

        Returns
        -------
        self : FrozenEstimator
            This estimator.
        """
        estimator = kwargs.pop("estimator", None)
        if estimator is not None:
            self.estimator = estimator
        if kwargs:
            raise ValueError(
                "You cannot set parameters of the inner estimator in a frozen "
                "estimator since calling `fit` has no effect. You can use "
                "`frozenestimator.estimator.set_params` to set parameters of the inner "
                "estimator."
            )

    def get_params(self, deep=True):
        """Get parameters for this estimator.

        Returns a `{"estimator": estimator}` dict. The parameters of the inner
        estimator are not included.

        Parameters
        ----------
        deep : bool, default=True
            Ignored.

        Returns
        -------
        params : dict
            Parameter names mapped to their values.
        """
        return {"estimator": self.estimator}

    def _more_tags(self):
        """Return tags for this estimator using the old tag system."""
        tags = deepcopy(_safe_tags(self.estimator))
        tags["_skip_test"] = True
        return tags
FROZEN_EOF

# [ENV-PATCH] Add missing _check_feature_names function to validation.py for START state
RUN cat >> /testbed/sklearn/utils/validation.py << 'VALIDATION_PATCH_EOF'


def _check_feature_names(estimator, X, *, reset):
    """Set or check the `feature_names_in_` attribute of an estimator.

    [ENV-PATCH] Stub implementation to allow test collection.
    """
    if reset:
        feature_names_in = _get_feature_names(X)
        if feature_names_in is not None:
            estimator.feature_names_in_ = feature_names_in
        elif hasattr(estimator, "feature_names_in_"):
            delattr(estimator, "feature_names_in_")
        return

    fitted_feature_names = getattr(estimator, "feature_names_in_", None)
    X_feature_names = _get_feature_names(X)

    if fitted_feature_names is None and X_feature_names is None:
        return

    if X_feature_names is not None and fitted_feature_names is None:
        warnings.warn(
            f"X has feature names, but {estimator.__class__.__name__} was fitted "
            "without feature names"
        )
        return

    if X_feature_names is None and fitted_feature_names is not None:
        warnings.warn(
            "X does not have valid feature names, but"
            f" {estimator.__class__.__name__} was fitted with feature names"
        )
        return

    if len(fitted_feature_names) != len(X_feature_names) or np.any(
        fitted_feature_names != X_feature_names
    ):
        message = "The feature names should match those that were passed during fit.\n"
        fitted_feature_names_set = set(fitted_feature_names)
        X_feature_names_set = set(X_feature_names)
        unexpected_names = sorted(X_feature_names_set - fitted_feature_names_set)
        missing_names = sorted(fitted_feature_names_set - X_feature_names_set)
        if unexpected_names:
            message += f"Unexpected feature names: {unexpected_names}\n"
        if missing_names:
            message += f"Missing feature names: {missing_names}\n"
        raise ValueError(message)
VALIDATION_PATCH_EOF

# [ENV-PATCH] Add is_clusterer function to base.py for START state
RUN cat >> /testbed/sklearn/base.py << 'BASE_PATCH_EOF'


def is_clusterer(estimator):
    """Return True if the given estimator is a clusterer.

    [ENV-PATCH] Stub implementation to allow test collection.
    """
    return getattr(estimator, "_estimator_type", None) == "clusterer"
BASE_PATCH_EOF

# Commit the patches and move the START tag to include the patches
RUN cd /testbed && \
    git add -A && \
    git commit -m "[ENV-PATCH] Add missing sklearn.frozen module and API stubs for START state" && \
    git tag -f milestone-M12.4-start HEAD

# Rebuild scikit-learn since we replaced the testbed
RUN pip install --no-build-isolation --editable . -v

# Default to START state
RUN cd /testbed && git checkout milestone-M12.4-start
