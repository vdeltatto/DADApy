# Copyright 2021 The DADApy Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================

import multiprocessing
import os

import matplotlib.pyplot as plt
import numpy as np
from scipy.stats import ks_2samp as KS

import dadapy._utils.discrete_functions as df
from dadapy.base import Base

cores = multiprocessing.cpu_count()
rng = np.random.default_rng()


class IdDiscrete(Base):
    """Estimates the intrinsic dimension of a dataset with discrete features using a binomial likelihood.

    Inherits from class Base.

    Attributes:

            lk (int, np.ndarray(int)): radius of the external shells
            ln (int, np.ndarray(int)): radius of the internal shells
            k (np.ndarray(int)): total number of points within the external shell
            n (np.ndarray(int)): total number of points within the internal shell
            ratio (float): ratio between internal and external radii
            intrinsic_dim (float): intrinsic dimension obtained with the binomial estimator
            intrinsic_dim_err (float): error associated with the id estimation. computed through Cramer-Rao or Bayesian inference
            intrinsic_dim_scale (float): scale at which the id has been computed
            posterior_domain (np.ndarray(float)): eventual support of the posterior distribution of the id
            posterior (np.ndarray(float)): posterior distribution when evaluated with Bayesian inference

    """

    def __init__(
        self,
        coordinates=None,
        distances=None,
        maxk=None,
        condensed=None,
        weights=None,
        verbose=False,
        njobs=cores,
    ):
        super().__init__(
            coordinates=coordinates,
            distances=distances,
            maxk=maxk,
            verbose=verbose,
            njobs=njobs,
        )

        if weights is None:
            self._is_w = False
            self._weights = None
        else:
            self.set_w(weights)
            self._is_w = True

        self.lk = None
        self.ln = None
        self.k = None
        self.n = None
        self.ratio = None

        self._k = None
        self._mask = None
        self._condensed = condensed

        self.intrinsic_dim = None
        self.intrinsic_dim_err = None
        self.intrinsic_dim_scale = None
        self.posterior_domain = None
        self.posterior = None

    # ----------------------------------------------------------------------------------------------

    def compute_distances(
        self, maxk=None, metric="manhattan", period=None, condensed=None, d_max=100
    ):
        """Compute distances between datapoints. Distances can be saved in an extended or compacted shape.

        If condensed is True, self.distances will store how many points are present up to that distance.
        Conversely, the usual scheme is adopted (see same method in Base)

        Args:
            maxk: maximum number of neighbours for which distance is computed and stored
            metric: type of metric
            period (float or np.array): periodicity (only used for periodic distance computation). Default is None.
            condensed (bool): save how many points one finds at each distance instead of all distances
            d_max (int, default=100): decide the farthest distance to be saved (only if condensed is True)

        """
        if condensed is not None:
            self._condensed = condensed
        else:
            assert (
                self._condensed is not None
            ), 'initialize "condensed" parameter in init or compute distances'

        if self._condensed:
            self.metric = metric

            self.period = period
            if period is not None:
                if isinstance(period, np.ndarray) and period.shape == (self.dims,):
                    self.period = np.array(period,dtype=int)
                elif isinstance(period, int):
                    self.period = np.full(self.dims, fill_value=period, dtype=int)
                else:
                    raise ValueError(
                        f"'period' must be either a float scalar or a numpy array of floats of shape ({self.dims},)"
                    )

            self.distances, self.dist_indices = df.return_condensed_distances(
                self.X, self.metric, d_max, self.period
            )

        else:
            super().compute_distances(maxk, metric, period)

    # ----------------------------------------------------------------------------------------------

    def fix_lk(self, lk=None, ln=None):
        """Computes the k points within the given Rk and n points within given Rn.

        For each point, computes the number self.k of points within a sphere of radius Rk
        and the number self.n within an inner sphere of radius Rk. It also provides
        a mask to take into account those points for which the statistics might be wrong, i.e.
        k == self.maxk, meaning that all available points were selected or when we are not
        sure to have all the point of the selected shell. If self.maxk is equal
        to the number of points of the dataset no mask will be applied, as all the point
        were surely taken into account.
        The mask is not necessary (i.e. is set to True) if condensed is True, as all points will
        be considered.
        Eventually add the weights of the points if they have an explicit multiplicity

        Args:
            lk (int): external shell radius
            ln (int): internal shell radius

        Returns:

        """
        # checks-in and intialisations
        assert (
            self.distances is not None
        ), "first compute distances with the proper metric (manhattan of hamming presumably)"

        if lk is not None and ln is not None:
            self.set_lk_ln(lk, ln)
        else:
            assert (
                self.lk is not None and self.ln is not None
            ), "set lk and ln or insert proper values for the lk and ln parameters"
        self.set_ratio(float(self.ln) / float(self.lk))


        if self._condensed:
            self.n = np.copy(self.distances[:, self.ln])
            self.k = np.copy(self.distances[:, self.lk])
            self._mask = np.ones(self.N, dtype=bool)

        else:

            if self._is_w is False:
                self.k = (self.distances <= self.lk).sum(axis=1)
                self.n = (self.distances <= self.ln).sum(axis=1)
            else:
                assert (
                    self._weights is not None
                ), "first insert the weights if you want to use them!"
                self.k = np.array(
                    [
                        sum(self._weights[self.dist_indices[i][el]])
                        for i, el in enumerate(self.distances <= self.lk)
                    ],
                    dtype=np.int,
                )
                self.n = np.array(
                    [
                        sum(self._weights[self.dist_indices[i][el]])
                        for i, el in enumerate(self.distances <= self.ln)
                    ],
                    dtype=np.int,
                )

            # checks-out
            self._k = 0  # just a flag to remember whether id was computed at constant radius \
            # or constant number of neighbours
            # compute mask
            if self.maxk == self.N - 1:
                self._mask = np.ones(self.N, dtype=bool)
            else:
                # if not all possible NN were taken into account (i.e. maxk < N) and k is equal to self.maxk
                # or distances[:,-1]<lk, it is likely that there are other points within lk that are not being
                # considered and thus potentially altering the statistics -> neglect them through self._mask
                # in the calculation of likelihood
                self._mask = self.distances[:, -1] > self.lk  # or self.k == self.maxk

                if np.any(~self._mask):
                    print(
                        "NB: for "
                        + str(sum(~(self._mask)))
                        + " points, the counting of k could be wrong, "
                        + "as more points might be present within the selected Rk. In order not to affect "
                        + "the statistics a mask is provided to remove them from the calculation of the "
                        + "likelihood or posterior.\nConsider recomputing NN with higher maxk or lowering Rk."
                    )
        if self.verb:
            print("n and k computed")

    # ----------------------------------------------------------------------------------------------

    def compute_id_binomial_lk(
        self, lk=None, ln=None, method="bayes", subset=None, plot=True, set_attr=True
    ):
        """Calculate Id using the binomial estimator by fixing the eternal radius for all the points

        In the estimation of d one has to remove the central point from the counting of n and k
        as it generates the process, but it is not effectively part of it

        Args:
            lk (int): radius of the external shell
            ln (int): radius of the internal shell
            subset (int): choose a random subset of the dataset to make the Id estimate
            method (str, default 'bayes'): choose method between 'bayes' and 'mle'. The bayesian estimate
                    gives the distribution of the id, while mle only the max of the likelihood
            plot (bool): if bayes method is used, one can decide whether to plot the posterior
            set_attr (bool): changes class attributes after computation

        Returns:
            intrinsic_dim (float): the id esteem
            intrinsic_dim_err (float): the error estimate on the id
            intrinsic_dim_scale (float): the scale at which the id was computed (lk)
        """
        # checks-in and initialisations
        assert (
            self.distances is not None
        ), "first compute distances with the proper metric (manhattan of hamming presumably)"

        if lk is not None and ln is not None:
            if isinstance(self.lk, np.ndarray):  # id previously computed by fixing k
                self.set_lk_ln(lk, ln)
                self.fix_lk()
            elif (
                lk != self.lk or ln != self.ln
            ):  # ln or lk changing from a previous estimation
                self.set_lk_ln(lk, ln)
                self.fix_lk()
        else:
            assert (
                self.lk is not None and self.ln is not None
            ), "set lk and ln through set_lk_ln or insert proper values for the lk and ln parameters"

        mask = self._my_mask(subset)
        n_eff = self.n[mask] - 1
        k_eff = self.k[mask] - 1

        if self._is_w:
            w_eff = self._weights[mask]

        # check statistics before performing id estimation
        if ~self._is_w:
            e_n = n_eff.mean()
            if e_n == 0.0:
                print(
                    "no points in the inner shell, returning 0. Consider increasing ln and possibly lk"
                )
                self.intrinsic_dim = 0
                self.intrinsic_dim_err = 0
                return 0, 0, self.lk

        # choice of the method
        if method == "mle":
            if self._is_w:  # necessary only if using the root finding method
                N = w_eff.sum()
                k_eff = sum(k_eff * w_eff) / float(N)
                n_eff = sum(n_eff * w_eff) / float(N)
            else:
                w_eff = 1
                N = k_eff.shape[0]
                k_eff = k_eff.mean()
                n_eff = n_eff.mean()

            # explicit computation of likelihood, not necessary when ln and lk are fixed, but apparently\
            # more stable than root searching
            intrinsic_dim = df.find_d_likelihood(self.ln, self.lk, n_eff, k_eff, w_eff)
            # intrinsic_dim = df.find_d_root(self.ln, self.lk, n_eff, k_eff)

            intrinsic_dim_err = (
                df.binomial_cramer_rao(
                    d=intrinsic_dim, ln=self.ln, lk=self.lk, N=N, k=k_eff
                )
                ** 0.5
            )

        elif method == "bayes":
            if self._is_w:
                k_tot = w_eff * k_eff
                n_tot = w_eff * n_eff
            else:
                k_tot = k_eff
                n_tot = n_eff

            if self.verb:
                print("starting bayesian estimation")

            (
                intrinsic_dim,
                intrinsic_dim_err,
                self.posterior_domain,
                self.posterior,
            ) = df.beta_prior_d(
                k_tot, n_tot, self.lk, self.ln, plot=plot, verbose=self.verb
            )
        else:
            print("select a proper method for id computation")
            return 0

        if set_attr:
            self.intrinsic_dim = intrinsic_dim
            self.intrinsic_dim_err = intrinsic_dim_err
            self.intrinsic_dim_scale = self.lk

        return intrinsic_dim, intrinsic_dim_err, self.lk

    # ----------------------------------------------------------------------------------------------

    def return_id_scaling(self, Lks, r=0.5, method="mle", subset=None, plot=True):
        """Compute the ID by varying the radii

        Args:
            Lks (list or np.ndarray(int)): external radii lk
            r (float, default = 0.5): ratio among ln and lk
            method (string, default='mle'): method to compute the id
            subset (np.ndarray(int) or int, default=None): indices of points to be used for the estimate
            plot (bool, default=True): whether to plot the id scaling

        Returns:
            ids (np.ndarray): intrinsic dimension at different scales
            ids_err (np.ndarray): error estimate on intrinsic dimension at different scales
        """
        ids = np.zeros_like(Lks, dtype=float)
        ids_e = np.zeros_like(Lks, dtype=float)

        for i, lk in enumerate(Lks):
            id_i, id_er, scale = self.compute_id_binomial_lk(
                lk, int(lk * r), method=method, subset=subset, set_attr=False
            )
            ids[i] = id_i
            ids_e[i] = id_er

        if plot:
            plt.plot(Lks, ids)
            plt.errorbar(Lks, ids, ids_e, fmt="None")
            plt.xlabel("scale")
            plt.ylabel("ID estimate")
            plt.show()

        return ids, ids_e

    # ----------------------------------------------------------------------------------------------

    def fix_k(self, k_eff=None, ratio=0.5):
        """Computes Rk, Rn, n for each point given a selected value of k

        This routine computes external radii lk, internal radii ln and internal points n.
        It also ensures that lk is scaled onto the most external shell
        for which we are sure to have all points.
        A mask will exclude "badly behaved" points if condensed is False.
        NB As a consequence the k will be point dependent

        Args:
            k_eff (int, default=self.k_max): selected (max) number of NN
            ratio (float, default = 0.5): approximate ratio among ln and lk

        Returns:
        """

        # TODO: what if we have multiplicity???? use it straightly on the plain points
        #       or take it into account when counting the number of NN?

        # checks-in and initialisations
        assert (
            self.distances is not None
        ), "first compute distances with the proper metric (manhattan of hamming presumably)"

        self.set_ratio(ratio)

        if self._condensed:
            self.lk = np.array(
                [np.where(ddi <= k_eff)[0][-1] for ddi in self.distances]
            )
            self.ln = np.rint(self.lk * self.ratio).astype(int)
            self.k = np.take_along_axis(self.distances, self.lk[:, None], 1)[:, 0]
            self.n = np.take_along_axis(self.distances, self.ln[:, None], 1)[:, 0]
            self._mask = np.ones(self.N, dtype=bool)

        else:

            if k_eff is None:
                k_eff = self.maxk - 1
            else:
                assert (
                    k_eff < self.maxk
                ), "A k_eff > maxk was selected, recompute the distances with the proper amount on NN to see points up to that k_eff"

            # routine
            self.lk, self.k, self.ln, self.n = (
                np.zeros(self.N),
                np.zeros(self.N, dtype=np.int64),
                np.zeros(self.N),
                np.zeros(self.N, dtype=np.int64),
            )
            self._mask = np.ones(self.N, dtype=bool)

            # cut distances at the k-th NN and cycle over each point
            for i, dist_i in enumerate(self.distances[:, : k_eff + 1]):

                # case 1: all possible neighbours have been considered, no extra possible unseen points
                if k_eff == self.N:
                    lk_temp = dist_i[-1]
                    k_temp = k_eff

                # case 2: only some NN are considered -> work on surely "complete" shells
                else:
                    # Group distances according to shell. Returns radii of shell and cumulative number of points at that radius.
                    # The index at which a distance is first met == number of points at smaller distance
                    # EXAMPLE:
                    # a = np.array([0,1,1,2,3,3,4,4,4,6,6])     and suppose it would go on as 6,6,6,7,8...
                    # un, ind = np.unique(a, return_index=True)
                    # un: array([0, 1, 2, 3, 4, 6])
                    # ind: array([0, 1, 3, 4, 6, 9])
                    unique, index = np.unique(dist_i, return_index=True)

                    if unique.shape[0] < 3:
                        # lk=0 may happen when we have smtg like dist_i = [0,0,0,0,0] or dist_i = [0,3,3,3,3,3]. As we don't have the full
                        # information for at least two shells, we skip the point
                        self._mask[i] = False
                        continue

                    # set lk to the distance of the second-to-last shell, for which we are sure to have full info
                    lk_temp = unique[-2]  # EX: 4
                    # set k to cumulative up to last complete shell
                    k_temp = index[-1]  # EX: 9

                # fix internal radius
                ln_temp = np.rint(lk_temp * self.ratio)
                # if the inner shell is accidentally the same of the outer, go to the innerer one
                if ln_temp == lk_temp:
                    ln_temp -= 1

                n_temp = sum(dist_i <= ln_temp)

                self.k[i] = k_temp.astype(np.int64)
                self.n[i] = n_temp.astype(np.int64)
                self.lk[i] = lk_temp
                self.ln[i] = ln_temp

            # checks out
            if any(~self._mask):
                print(
                    "BE CAREFUL: "
                    + str(sum(~self._mask))
                    + " points would have lk set to 0 "
                    + "and thus will not be considered when computing the id. Consider increasing k."
                )

        self._k = k_eff
        if self.verb:
            print("n and k computed")

    # --------------------------------------------------------------------------------------

    def fix_k_shell(self, k_shell, ratio):
        """Computes the lk, ln, n given k_shell

        This routine computes the external radius lk, the associated points k, internal radius ln and associated points n.
        The computation is performed starting from a given number of shells.
        It ensures that Rk is scaled onto the most external shell for which we are sure to have all points.
        NB in this case we will have an effective, point dependent k

        Args:
            k_shell (int): selected (max) number of considered shells
            ratio (float): ratio among Rn and Rk

        """

        # TODO: one might want to use it even with less shells available

        # initial checks
        assert (
            self.distances is not None
        ), "first compute distances with the proper metric (manhattan of hamming presumably)"

        self.set_ratio(ratio)

        if self._condensed:
            assert k_shell < self.distances.shape[1]
            self.lk = np.array(
                [
                    np.where(ddi != 0)[0][k_shell]
                    for ddi in self.distances[:, 1:] - self.distances[:, :-1]
                ]
            )
            self.ln = np.rint(self.lk * self.ratio).astype(int)
            self.k = np.take_along_axis(self.distances, self.lk[:, None], 1)[:, 0]
            self.n = np.take_along_axis(self.distances, self.ln[:, None], 1)[:, 0]
            self._mask = np.ones(self.N, dtype=bool)

        else:
            assert k_shell < self.maxk, "asking for a much too large number of shells"

            self.lk, self.k, self.ln, self.n = (
                np.zeros(self.N),
                np.zeros(self.N, dtype=np.int64),
                np.zeros(self.N),
                np.zeros(self.N, dtype=np.int64),
            )
            self._mask = np.ones(self.N, dtype=bool)

            for i, dist_i in enumerate(self.distances):

                unique, index = np.unique(dist_i, return_index=True)

                # check whether the point has enough shells, at least one more than the one we want to consider
                if unique.shape[0] < k_shell + 1:
                    self._mask[i] = False
                    continue  # or lk_temp = unique[-1] even if the shell is not the wanted one???

                # set lk to the distance of the selected shell
                lk_temp = unique[k_shell]
                # and ln according to the ratio
                ln_temp = np.rint(lk_temp * self.ratio)
                # if the inner shell is accidentally the same of the outer, go to the innerer one
                if ln_temp == lk_temp:
                    ln_temp -= 1

                # compute k and n
                if self._is_w:
                    which_k = dist_i <= lk_temp
                    self.k[i] = sum(
                        self._weights[self.dist_indices[i][which_k]]
                    ).astype(np.int64)
                    which_n = dist_i <= ln_temp
                    self.n[i] = sum(
                        self._weights[self.dist_indices[i][which_n]]
                    ).astype(np.int64)
                else:
                    self.k[i] = index[k_shell + 1].astype(np.int64)
                    self.n[i] = sum(dist_i <= ln_temp).astype(np.int64)

                self.lk[i] = lk_temp
                self.ln[i] = ln_temp

            # checks out
            if any(~self._mask):
                print(
                    "BE CAREFUL: "
                    + str(sum(~self._mask))
                    + " points would have Rk set to 0 "
                    + "and thus will be kept out of the statistics. Consider increasing k."
                )
        self._k = k_shell
        if self.verb:
            print("n and k computed")

    # --------------------------------------------------------------------------------------

    def compute_id_binomial_k(
        self, k, shell=False, ratio=None, subset=None, set_attr=True
    ):
        """Calculate Id using the binomial estimator by fixing the number of neighbours or shells

        As in the case in which one fix lk, also in this version of the estimation
        one removes the central point from n and k. Two different ways of computing
        k,n,lk,ln are available, whether one intends k as the k-th neighbour
        or the k-th shell. In both cases, one wants to be sure that the shell
        chosen is

        Args:
            k (int): order of neighbour that set the external shell
            shell (bool): k stands for number of neighbours or number of occupied shells
            ratio (float): ratio between internal and external shell
            subset (int): choose a random subset of the dataset to make the Id estimate
            set_attr (bool, default=True): assign id, id error and scale to the class

        Returns:
            intrinsic_dim (float): the id esteem
            intrinsic_dim_err (float): the error estimate on the id
            intrinsic_dim_scale (float): the scale at which the id was computed: <lk>
        """
        # checks-in and initialisations
        assert (
            self.distances is not None
        ), "first compute distances with the proper metric (manhattan of hamming presumably)"

        if k != self._k or ratio != self.ratio:
            if shell:
                self.fix_k_shell(k, ratio)
            else:
                self.fix_k(k, ratio)

        mask = self._my_mask(subset)

        n_eff = self.n[mask] - 1
        k_eff = self.k[mask] - 1
        ln_eff = self.ln[mask]
        lk_eff = self.lk[mask]
        if self._is_w:
            ww = self._weights[mask]
        else:
            ww = 1

        e_n = n_eff.mean()
        if e_n == 0.0:
            print(
                "no points in the inner shell, returning 0. Consider increasing lk and/or the ratio"
            )
            self.intrinsic_dim = 0
            self.intrinsic_dim_err = 0
            return 0, 0, lk_eff.mean()

        intrinsic_dim = df.find_d_likelihood(ln_eff, lk_eff, n_eff, k_eff, ww)
        a, b, c, d = df.profile_likelihood(ln_eff, lk_eff, n_eff, k_eff, ww)
        intrinsic_dim_err = b

        if set_attr:
            self.intrinsic_dim = intrinsic_dim
            self.intrinsic_dim_err = intrinsic_dim_err
            self.intrinsic_dim_scale = lk_eff.mean()

        return intrinsic_dim, intrinsic_dim_err, lk_eff.mean()

    # ----------------------------------------------------------------------------------------------

    def return_id_scaling_k(self, Ks, r=0.5, subset=None, plot=True):
        """Compute the ID by varying the number of neighbours considered

        Args:
            Ks (list or np.ndarray(int)): number of neighbours
            r (float, default = 0.5): ratio between ln and lk
            subset (int): choose a random subset of the dataset to make the Id estimate
            plot (bool, default=True): whether to plot the id as a function of Ks

        Returns:
            ids (np.ndarray(float)): intrinsic dimension at different scales
            ids_err (np.ndarray(float)): error estimate on intrinsic dimension at different scales
            scale (np.ndarray(float)): scale at which the id was computed (mean values of lk)
        """
        ids = np.zeros_like(Ks, dtype=float)
        ids_e = np.zeros_like(Ks, dtype=float)
        scale = np.zeros_like(Ks, dtype=float)

        for i, k in enumerate(Ks):
            ids[i], ids_e[i], scale[i] = self.compute_id_binomial_k(
                k, False, r, subset=subset, set_attr=False
            )

        if plot:
            plt.plot(Ks, ids)
            plt.errorbar(Ks, ids, ids_e, fmt="None")
            plt.xlabel("scale")
            plt.ylabel("ID estimate")
            plt.show()

        return ids, ids_e, scale

    # -------------------------------MODEL VALIDATION-----------------------------------------------
    # ----------------------------------------------------------------------------------------------

    def compute_local_density(self, plot=True, path=None):
        """Compute and compare the rescaled local density of points

        Compute and compare the local density of points inside inner and outer shells
        after fixing lk and ln as n/<n> and (k-n)/<k-n>.
        The closest the points to the y=x line, the more the local uniform density
        hypothesis is respected, the better will be the id estimate

        Args:
            plot (bool, default=True): decide whether to plot the local density
            path (string, deafult=None): filename to save the plot
        Returns:
            n (np.ndarray(float)): n/<n>
            m (np.ndarray(float)): (k-n)/<k-n>
        """

        assert self.n is not None and self.k is not None, "find k and n first!"
        assert self.intrinsic_dim is not None, "compute the ID first!"

        n = np.copy(self.n).astype(float)
        m = np.copy(self.k - self.n).astype(float)

        # id computed at fixed lk
        if self._k == 0:
            assert isinstance(self.lk, int)
            n /= n.mean()
            m /= m.mean()

        # id computed at fixed k
        else:
            Vk = df.compute_discrete_volume(self.lk, self.intrinsic_dim)
            Vn = df.compute_discrete_volume(self.ln, self.intrinsic_dim)
            n /= Vn
            m /= Vk - Vn

        dist = abs(n - m) / np.sqrt(n**2 + m**2)
        print("average distance from line: ", dist.mean())

        if plot:
            plt.scatter(n, m, s=1.0)
            plt.plot(
                np.linspace(min(min(n), min(m)), max(max(n), max(m)), 100),
                np.linspace(min(min(n), min(m)), max(max(n), max(m)), 100),
            )
            plt.xlabel(r"$n/\langle n \rangle$")
            plt.ylabel(r"$(k-n)/\langle k-n \rangle$")

        if path is not None:
            path = path.rstrip("/") + "/"
            os.system("mkdir -p " + path)
            plt.savefig(path + "local_density.pdf")

        return n, m

    # ----------------------------------------------------------------------------------------------

    def model_validation_full(
        self, alpha=0.05, subset=None, pdf=False, cdf=True, path=None
    ):
        """Use Kolmogorov-Smirnoff test to assess the goodness of the estimate

        In order to validate estimate of the intrinsic dimension and the model
        and the goodness of the binomial estimator we perform a KS test. In
        particular, once the ID has been computed, we generate a new set n_i
        starting from the k_i, lk_, ln_i and d using the binomial distribution
        (ln and lk can be scalars if the id estimate has been performed with
        fixed radii).
        We then compare the CDF obtained from both the n_empirical and the new
        n_i using the KS test, looking at the maximum distance between the two
        CDF

        Args:
            alpha (float, default=0.05): tolerance for the KS test
            subset (int or np.ndarray(int)): subset of points used to perform the model validation
            pdf (bool, default=False): plot histogram of n_emp and n_i
            cdf (bool, default=False): plot cdf of n_emp and n_i
            path (str, default=None): directory where to save plots
        """
        if self.intrinsic_dim is None:
            print("compute the id before validating the model!")
            return 0

        if path is not None:
            path = path.rstrip("/") + "/"
            os.system("mkdir -p " + path)

        mask = self._my_mask(subset)
        n_eff = self.n[mask] - 1
        k_eff = self.k[mask] - 1

        if isinstance(self.ln, np.ndarray):  # id estimated at fixed K

            ln_eff = self.ln[mask]
            lk_eff = self.lk[mask]

            title = "K=" + str(self._k)

            p = df.compute_discrete_volume(
                ln_eff, self.intrinsic_dim
            ) / df.compute_discrete_volume(lk_eff, self.intrinsic_dim)

        else:  # id estimated at fixed lK

            title = "R=" + str(self.lk)

            p = df.compute_discrete_volume(
                self.ln, self.intrinsic_dim
            ) / df.compute_discrete_volume(self.lk, self.intrinsic_dim)

        n_model = rng.binomial(k_eff, p, size=mask.sum())

        s, pv = KS(n_eff, n_model)

        print("ks_stat:\t" + str(s))
        print("p-value:\t" + str(pv))

        if self.verb:
            if pv > alpha:
                print(
                    "We cannot reject the null hypothesis: the empirical and theoretical\
                    distributions has to be considered equivalent"
                )
            else:
                print(
                    "We have to reject the null hypothesis: the two distributions are not\
                    equivalent and thus the model as it is cannot be used to infer the id"
                )
        if pdf or cdf:
            sup = max(n_eff.max(), n_model.max())
            inf = min(n_eff.min(), n_model.min())
            a = np.histogram(
                n_eff, range=(inf - 0.5, sup + 0.5), bins=sup - inf + 1, density=True
            )
            b = np.histogram(
                n_model, range=(inf - 0.5, sup + 0.5), bins=sup - inf + 1, density=True
            )

        if pdf:
            if path is not None:
                fileout = path + "mv_pdf.pdf"
            else:
                fileout = None
            df.plot_pdf(a[0], b[0], title, fileout)

        if cdf:
            cdf_nn = np.cumsum(a[0])
            cdf_nn = cdf_nn / cdf_nn[-1]
            cdf_nmod = np.cumsum(b[0])
            cdf_nmod = cdf_nmod / cdf_nmod[-1]
            if path is not None:
                fileout = path + "mv_cdf.pdf"
            else:
                fileout = None
            df.plot_cdf(cdf_nn, cdf_nmod, title, fileout)

        return pv

    # ----------------------------------------------------------------------------------------------

    def R_mod_val(
        self, k_win, subset=None, path=None, pdf=False, cdf=True, recap=False
    ):
        """Use Kolmogorov-Smirnoff test to assess the goodness of the estimate at fixed R within certain windows in k

        For a fixed value of R and for given values of windows of k, compute
        artificial estimates for the n and compare them to the empirical ones
        using the KS test

        Args:
            k_win (np.ndarray(int)): boundaries of the windows in k
            subset (int or np.ndarray(int)): subset of points used to perform the model validation
            path (string): if provided, save the plots and observables to the provided folder
            pdf (bool): plot empirical vs model pdfs
            cdf (bool): plot empirical vs model cdfs
            recap (bool): plot id and p-values for each window
        """
        if path is not None:
            path = path.rstrip("/") + "/"
            os.system("mkdir -p " + path)
        obs = []
        mask = self._my_mask(subset)
        n_eff = self.n[mask]  # - 1
        k_eff = self.k[mask]  # - 1

        for i in range(len(k_win) - 1):

            # find points within window
            k_ave = (k_win[i + 1] + k_win[i]) / 2.0
            mask_i = np.logical_and(k_win[i] < k_eff, k_eff <= k_win[i + 1])
            ki = k_eff[mask_i]
            ni = n_eff[mask_i]

            # skip window with too few points
            if len(ni) < 10:
                continue

            # find id and error within window
            # if ni.mean() < k_ave:
            #     ki_ = k_ave
            # else:
            #     ki_ = ki.mean()
            # id_i = df.find_d(self.ln, self.lk, ni.mean(), ki_)
            id_i = df.find_d_root(self.ln, self.lk, ni.mean(), ki.mean())
            cr = (
                df.binomial_cramer_rao(id_i, self.ln, self.lk, len(ni), ki.mean())
                ** 0.5
            )

            ## model validation
            # compute the p of the binomial distribution
            p = df.compute_discrete_volume(self.ln, id_i) / df.compute_discrete_volume(
                self.lk, id_i
            )

            # extract the artificial n
            # n_mod = rng.binomial(k_ave, p, size=mask_i.sum())
            n_mod = rng.binomial(ki, p)  # size=mask_i.sum()
            # produce histograms
            sup = max(ni.max(), n_mod.max())
            inf = min(ni.min(), n_mod.min())
            a = np.histogram(
                ni, range=(inf - 0.5, sup + 0.5), bins=sup - inf + 1, density=True
            )
            b = np.histogram(
                n_mod, range=(inf - 0.5, sup + 0.5), bins=sup - inf + 1, density=True
            )

            # perform KS test
            kstat, pvi = KS(n_mod, ni)
            print(
                "window elements:\t",
                len(ni),
                "\nwindow id:\t",
                id_i,
                "\nwindow p-value:\t",
                pvi,
            )
            # save observables
            obs.append(
                (
                    k_ave,
                    id_i,
                    cr,
                    ni.mean(),
                    ni.std(),
                    n_mod.mean(),
                    n_mod.std(),
                    len(ni),
                    pvi,
                )
            )
            # PLOT
            title = r"R=" + str(self.lk) + "\t$\overline{k}=$" + str(k_ave)
            # DATA------------------------------------------------------
            # plt.figure()
            # plt.title('values')
            # plt.plot(kk,alpha=0.5,label='k emp')
            # plt.plot(nn,alpha=0.5,label='n emp')
            # plt.plot(n_mod,alpha=0.5,label='n mod')
            # plt.plot(n_mod1,alpha=0.5,label='n mod1')
            # plt.xlabel('window index')
            # plt.legend()
            # plt.show()
            # PDF--------------------------------------------------------
            if pdf:
                if path is not None:
                    fileout = path + "R" + str(k_ave) + "_pdf.pdf"
                else:
                    fileout = None
                df.plot_pdf(a[0], b[0], title, fileout)

            # CDF-------------------------------------------------------
            if cdf:
                cdf_nn = np.cumsum(a[0])
                cdf_nn = cdf_nn / cdf_nn[-1]
                cdf_nmod = np.cumsum(b[0])
                cdf_nmod = cdf_nmod / cdf_nmod[-1]
                if path is not None:
                    fileout = path + "k" + str(k_ave) + "_cdf.pdf"
                else:
                    fileout = None
                df.plot_cdf(cdf_nn, cdf_nmod, title, fileout)

            # plt.figure()
            # plt.scatter(self.X[:, 0], self.X[:, 1], alpha=0.2)
            # plt.scatter(self.X[mask_i, 0], self.X[mask_i, 1], alpha=0.2, color="red")
            # name = path + "k" + str(k_ave) + "_proj.png"
            # plt.savefig(name, dpi=300)
            # #plt.show()

        obs = np.array(obs)
        if recap:
            if path is not None:
                fileout = path + "id_pv.pdf"
            else:
                fileout = None
            df.plot_id_pv(
                obs[:, 0],
                obs[:, 1],
                obs[:, -1],
                "R=" + str(self.lk),
                r"$\overline{k}$",
                fileout,
            )

        if path:
            np.savetxt(
                path + "observables.txt",
                obs,
                header="<k_win>\tid\tstd(id)\t<n emp>\tstd(n emp)\t<n mod>\tstd(n mod)>\telem\tp-value",
                fmt="%.3f",
                delimiter="\t",
            )
        else:
            print(
                "<k_win>\tid\tstd(id)\t<n emp>\tstd(n emp)\t<n mod>\tstd(n mod)>\telem\tp-value"
            )
            for ob in obs:
                print(ob)

    # ----------------------------------------------------------------------------------------------
    def K_mod_val(
        self, R_win, subset=None, path=None, pdf=False, cdf=True, recap=False
    ):
        """Use Kolmogorov-Smirnoff test to assess the goodness of the estimate at fixed R within certain windows in k

        For a fixed value of K and for given values of windows of R, compute
        artificial estimates for the n and compare them to the empirical ones
        using the KS test

        Args:
            R_win (np.ndarray(int)): boundaries of the windows in R
            subset (int or np.ndarray(int)): subset of points used to perform the model validation
            path (string): if provided, save the plots and observables to the provided folder
            pdf (bool): plot empirical vs model pdfs
            cdf (bool): plot empirical vs model cdfs
            recap (bool): plot id and p-values for each window
        """
        if path is not None:
            path = path.rstrip("/") + "/"
            os.system("mkdir -p " + path)
        obs = []

        mask = self._my_mask(subset)
        n_eff = self.n[mask] - 1
        k_eff = self.k[mask] - 1
        ln_eff = self.ln[mask]
        lk_eff = self.lk[mask]

        for i in range(len(R_win) - 1):

            # find points within window
            R_ave = (R_win[i + 1] + R_win[i]) / 2.0
            mask_i = np.logical_and(R_win[i] < lk_eff, lk_eff <= R_win[i + 1])
            # mask_i = mask_i * (
            #     k_eff >= 0.8 * k_eff.max()
            # )  # selects only those points for which k_eff >~ 0.8 K
            ki = k_eff[mask_i]
            ni = n_eff[mask_i]
            lki = lk_eff[mask_i]
            lni = ln_eff[mask_i]

            k_ave = ki.mean()
            # skip window with too few points
            if len(ni) < 10:
                continue

            # find id and error within window
            # id_i = df.find_d_root(np.rint(R_ave * self.ratio), np.rint(R_ave), ni.mean(), k_ave)
            id_i = df.find_d_likelihood(lni, lki, ni, ki, 1)
            cr = (
                df.binomial_cramer_rao(
                    id_i, np.rint(R_ave * self.ratio), np.rint(R_ave), len(ni), k_ave
                )
                ** 0.5
            )

            ## model validation
            # compute the p of the binomial distribution
            # p = df.compute_discrete_volume(np.rint(R_ave * self.ratio), id_i) /\
            #    df.compute_discrete_volume(np.rint(R_ave), id_i)
            p = df.compute_discrete_volume(lni, id_i) / df.compute_discrete_volume(
                lki, id_i
            )
            # extract the artificial n
            # n_mod = rng.binomial(k_ave, p, size=mask_i.sum())
            n_mod = rng.binomial(ki, p)  # size=mask_i.sum())
            # produce histograms
            sup = max(ni.max(), n_mod.max())
            inf = min(ni.min(), n_mod.min())
            a = np.histogram(
                ni, range=(inf - 0.5, sup + 0.5), bins=sup - inf + 1, density=True
            )
            b = np.histogram(
                n_mod, range=(inf - 0.5, sup + 0.5), bins=sup - inf + 1, density=True
            )

            # perform KS test
            kstat, pvi = KS(n_mod, ni)
            print(
                "window elements:\t",
                len(ni),
                "\nwindow id:\t",
                id_i,
                "\nwindow p-value:\t",
                pvi,
            )
            # save observables
            obs.append(
                (
                    R_ave,
                    id_i,
                    cr,
                    ni.mean(),
                    ni.std(),
                    n_mod.mean(),
                    n_mod.std(),
                    len(ni),
                    pvi,
                )
            )
            # PLOT
            title = r"K=" + str(self._k) + "\t$\overline{R}=$" + str(R_ave)
            # DATA------------------------------------------------------
            # plt.figure()
            # plt.title('values')
            # plt.plot(kk,alpha=0.5,label='k emp')
            # plt.plot(nn,alpha=0.5,label='n emp')
            # plt.plot(n_mod,alpha=0.5,label='n mod')
            # plt.plot(n_mod1,alpha=0.5,label='n mod1')
            # plt.xlabel('window index')
            # plt.legend()
            # plt.show()
            # PDF--------------------------------------------------------
            if pdf:
                if path is not None:
                    fileout = path + "R" + str(R_ave) + "_pdf.pdf"
                else:
                    fileout = None
                df.plot_pdf(a[0], b[0], title, fileout)
            # CDF-------------------------------------------------------
            if cdf:
                cdf_nn = np.cumsum(a[0])
                cdf_nn = cdf_nn / cdf_nn[-1]
                cdf_nmod = np.cumsum(b[0])
                cdf_nmod = cdf_nmod / cdf_nmod[-1]
                if path is not None:
                    fileout = path + "R" + str(R_ave) + "_cdf.pdf"
                else:
                    fileout = None
                df.plot_cdf(cdf_nn, cdf_nmod, title, fileout)

            # plt.figure()
            # plt.scatter(self.X[:, 0], self.X[:, 1], alpha=0.2)
            # plt.scatter(self.X[mask_i, 0], self.X[mask_i, 1], alpha=0.2, color='red')
            # # plt.show()
            # name = path + "R" + str(R_ave) + "_proj.png"
            # plt.savefig(name, dpi=300)

        obs = np.array(obs)
        if recap:
            if path is not None:
                fileout = path + "id_pv.pdf"
            else:
                fileout = None
            df.plot_id_pv(
                obs[:, 0],
                obs[:, 1],
                obs[:, -1],
                "K=" + str(self._k),
                r"$\overline{R}$",
                fileout,
            )

        if path:
            np.savetxt(
                path + "observables.txt",
                obs,
                header="<R_win>\tid\tstd(id)\t<n emp>\tstd(n emp)\t<n mod>\tstd(n mod)>\telem\tp-value",
                fmt="%.3f",
                delimiter="\t",
            )
        else:
            print(
                "<R_win>\tid\tstd(id)\t<n emp>\tstd(n emp)\t<n mod>\tstd(n mod)>\telem\tp-value"
            )
            for ob in obs:
                print(ob)

    # ----------------------------------------------------------------------------------------------

    def set_id(self, d):
        """Set id manually
        Args:
            id (float): intrisic dimension to assign to the class

        """

        assert d > 0, "cannot support negative dimensions (yet)"
        self.intrinsic_dim = d

    # ----------------------------------------------------------------------------------------------

    def set_lk_ln(self, lk, ln):
        """Assign lk and ln to class
        Args:
            lk (int): radius of external shells
            ln (int): radiuf of internal shells
        """

        assert (
            isinstance(ln, (np.int, np.int8, np.int16, np.int32, np.int64, int))
            and ln >= 0
        ), "select a proper integer ln>=0"
        assert (
            isinstance(lk, (np.int, np.int8, np.int16, np.int32, np.int64, int))
            and lk > 0
        ), "select a proper integer lk>0"
        assert lk > ln, "select lk and ln, s.t. lk > ln"
        self.ln = ln
        self.lk = lk

    # ----------------------------------------------------------------------------------------------

    def set_ratio(self, r):
        """Set ratio between internal and external shells
        Args:
            r (float): ratio, 0<r<1 is required
        """
        assert isinstance(r, float) and 0 <= r < 1, "select a proper ratio 0<r<1"
        self.ratio = r

    # ----------------------------------------------------------------------------------------------

    def set_w(self, w):
        """Set weights of points
        Args:
            w (np.ndarray(int)): multiplicity of points. Needs to have the same length of points
        """
        assert len(w) == self.N and all(
            [wi > 0 and isinstance(wi, (np.int, int))] for wi in w
        ), "load proper integer weights"
        self._weights = np.array(w, dtype=np.int)

    # ----------------------------------------------------------------------------------------------

    def _my_mask(self, subset=None):
        """Compute the mask to be used when computing the id

        This function create a mask used to compute the id on a subset of datapoints.
        It takes into account mask given by the fix_k or fix_lk methods and integrate
        it with the subset given as an argument. This can be an integer (and points
        will be chosen randomly) or a list/np.ndarray of indices.
        If subset is left empty, just passes the mask from fix_k or fix_lk

        Args:
            subset (int or np.ndarray(int) or list(int), default=None)

        Returns:
            my_mask (np.ndarray(bool)): mask indicating which points will be used in id computation
        """

        assert self._mask is not None

        if subset is None:
            return np.copy(self._mask)

        if isinstance(subset, (np.ndarray, list)):

            assert isinstance(
                subset[0], (int, np.integer)
            ), "elements of list/array must be integers, in order to be used as indexes"
            assert (
                max(subset) < self.N
            ), "the array must contain elements with indexes lower than the total number of elements"

            my_mask = np.zeros(self._mask.shape[0], dtype=bool)
            my_mask[subset] = True
            my_mask *= self._mask  # remove points with bad statistics

        elif isinstance(subset, (int, np.integer)):

            if subset > self._mask.sum():
                my_mask = np.copy(self._mask)

            else:
                my_mask = np.zeros(self._mask.shape[0], dtype=bool)
                idx = np.sort(
                    rng.choice(
                        np.where(self._mask == True)[0],
                        subset,
                        replace=False,
                        shuffle=False,
                    )
                )
                #                print(idx)
                my_mask[idx] = True

                assert my_mask.sum() == subset

        else:
            print("use a proper format for the subset, returning no subset")
            return np.copy(self._mask)

        return my_mask


# ----------------------------------------------------------------------------------------------


# if __name__ == '__main__':
#     X = rng.uniform(size = (1000, 2))
#
#     ide = IdEstimation(coordinates=X)
#
#     ide.compute_distances(maxk = 10)
#
#     ide.compute_id_2NN(decimation=1)
#
#     print(ide.id_estimated_2NN,ide.intrinsic_dim)
