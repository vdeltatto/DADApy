import os

import matplotlib.pyplot as plt
import numpy as np
import scipy
from matplotlib import cm
from scipy.optimize import minimize_scalar as SMin
from scipy.special import binom, gamma, hyp2f1

cmap = cm.get_cmap("tab20b", 20)

# --------------------------------------------------------------------------------------
# bounds for numerical estimation, change if needed
D_MAX = 70.0
D_MIN = np.finfo(np.float32).eps

# load, just once and for all, the coefficients for the polynomials in d at fixed L


volumes_path = os.path.join(os.path.split(__file__)[0], "discrete_volumes")
coeff = np.loadtxt(volumes_path + "/L_coefficients_float.dat", dtype=np.float64)

# V_exact_int = np.loadtxt(volume_path + '/V_exact.dat',dtype=np.uint64)

# --------------------------------------------------------------------------------------


def compute_discrete_volume(l, d, O1=False):
    """Enumerate the points contained in a region of radius L according to Manhattan metric

    Args:
        l (nd.array( integer or float )): radii of the volumes of which points will be enumerated
        d (float): dimension of the metric space


    Returns:
        V (nd.array( integer or float )): points within the given volumes

    """
    # OLD DEFINITIONS using series expansion with eventual approximation for large L

    # O1 (bool, default=False): first order approximation in the large L limit. Set to False in order to have the o(1/L) approx
    # if L is one dimensional make it an array
    # if isinstance(L, (int, np.integer, float, np.float)):
    #     l = [l]

    # # explicit conversion to array of integers
    # l = np.array(L, dtype=np.int)

    # # exact formula for integer d, cannot be used for floating values
    # if isinstance(d, (int, np.integer)):
    #     V = 0
    #     for k in range(0, d + 1):
    #         V += scipy.special.binom(d, k) * scipy.special.binom(l - k + d, d)
    #     return V

    # else:
    #     # exact enumerating formula for non integer d. Use the loaded coefficients to compute
    #     # the polynomials in d at fixed (small) L.
    #     # Exact within numerical precision, as far as the coefficients are available
    #     def V_polynomials(ll):
    #         D = d ** np.arange(coeff.shape[1], dtype=np.double)
    #         V_poly = np.dot(coeff, D)
    #         return V_poly[ll]

    #     # Large L approximation obtained using Stirling formula
    #     def V_Stirling(ll):
    #         if O1:
    #             correction = 2**d
    #         else:
    #             correction = (
    #                 np.exp(0.5 * (d + d**2) / ll) * (1 + np.exp(-d / ll)) ** d
    #             )

    #         return ll**d / scipy.special.factorial(d) * correction

    #     ind_small_l = l < coeff.shape[0]
    #     V = np.zeros(l.shape[0])
    #     V[ind_small_l] = V_polynomials(l[ind_small_l])
    #     V[~ind_small_l] = V_Stirling(l[~ind_small_l])

    #     return V

    return binom(d + l, d) * hyp2f1(-d, -l, -d - l, -1)


# --------------------------------------------------------------------------------------


def _compute_derivative_discrete_vol(l, d):
    """compute derivative of discrete volumes with respect to dimension

    Args:
        l (int): radii at which the derivative is calculated
        d (float): embedding dimension

    Returns:
        dV_dd (ndarray(float) or float): derivative at different values of radius

    """

    # TODO: write derivative of expression above. Not that simple to derive the hypergeometric
    #       probably need SymPy

    # exact formula with polynomials, for small L
    #    assert isinstance(l, (int, np.int))
    if l < coeff.shape[0]:
        l = int(l)

        D = d ** np.arange(-1, coeff.shape[1] - 1, dtype=np.double)

        coeff_d = coeff[l] * np.arange(
            coeff.shape[1]
        )  # usual coefficient * coeff from first deriv
        return np.dot(coeff_d, D)

        # faster version in case of array l, use 'if all(l<coeff.shape[0])'
        # else:
        # 	L = np.array(L, dtype=np.int)
        # 	coeff_d = coeff*np.arange(coeff.shape[1])
        # 	dV_dd = np.dot(coeff_d, D)
        # 	return dV_dd[L]
    # approximate definition for large L
    else:

        return (
            np.e ** (((0.5 + 0.5 * d) * d) / l)
            * (1 + np.e ** (-d / l)) ** d
            * l**d
            * (
                scipy.special.factorial(d)
                * (
                    (0.5 + d) / l
                    - d / (l + np.e ** (d / l) * l)
                    + np.log(1.0 + np.e ** (-(d / l)))
                    + np.log(l)
                )
                - d * scipy.special.gamma(d) * scipy.special.digamma(1 + d)
            )
        ) / scipy.special.factorial(d) ** 2


# --------------------------------------------------------------------------------------


def _compute_jacobian(lk, ln, d):
    """Compute jacobian of the ratio of volumes wrt d

    Given that the probability of the binomial process is p = V(ln,d)/V(lk,d), in order to
    obtain relationships for d (like deriving the LogLikelihood or computing the posterior)
    one needs to compute the differential dp/dd

    Args:
        lk (int): radius of external volume
        ln (int): radius of internal volume
        d (float): embedding dimension

    Returns:
        dp_dd (ndarray(float) or float): differential

    """
    # p = Vn / Vk
    Vk = compute_discrete_volume(lk, d)  # [0]
    Vn = compute_discrete_volume(ln, d)  # [0]
    dVk_dd = _compute_derivative_discrete_vol(lk, d)
    dVn_dd = _compute_derivative_discrete_vol(ln, d)
    dp_dd = dVn_dd / Vk - dVk_dd * Vn / Vk / Vk
    return dp_dd


# --------------------------------------------------------------------------------------


def _compute_binomial_logl(d, Rk, k, Rn, n, discrete=True, w=1):
    """Compute the binomial log likelihood given Rk,Rn,k,n

    Args:
        d (float): embedding dimension
        Rk (np.ndarray(float) or float): external radii
        k (np.ndarray(int) or int): number of points within the external radii
        Rn (np.ndarray(float) or float): external radii
        n (np.ndarray(int)): number of points within the internal radii
        discrete (bool, default=False): choose discrete or continuous volumes formulation
        w (np.ndarray(int or float), default=1): weights or multiplicity for each point

    Returns:
        -LogL (float): minus total likelihood

    """

    if discrete:
        p = compute_discrete_volume(Rn, d) / compute_discrete_volume(Rk, d)

    else:
        p = (Rn / Rk) ** d

    if np.any(p == 0):
        print("something went wrong in the calculation of p: check radii and d used")

    # the binomial coefficient is present within the definition of the likelihood,\
    # however it enters additively into the LogL. As such it does not modify its shape.\
    # Neglected if we need only to maximize the LogL

    # log_binom = np.log(scipy.special.binom(k, n))

    # for big values of k and n (~1000) the binomial coefficients explode -> use
    # its logarithmic definition through Stirling approximation

    # if np.any(log_binom == np.inf):
    #     mask = np.where(log_binom == np.inf)[0]
    #     log_binom[mask] = ut.log_binom_stirling(k[mask], n[mask])

    LogL = n * np.log(p) + (k - n) * np.log(1.0 - p)  # + log_binom
    # add weights contribution
    LogL = LogL * w
    # returns -LogL in order to be able to minimise it through scipy
    return -LogL.sum()


# --------------------------------------------------------------------------------------


def binomial_cramer_rao(d, ln, lk, N, k):
    """Calculate the Cramer Rao lower bound for the variance associated with the binomial estimator

    Args:
        d (float): space dimension
        ln (int): radius of the external shell
        lk (int): radius of the internal shell
        N (int): number of points of the dataset
        k (float): average number of neighbours in the external shell

    Returns:
        cr (float): the Cramer-Rao estimation
    """

    p = compute_discrete_volume(ln, d) / compute_discrete_volume(lk, d)

    return p * (1 - p) / (np.float64(N) * _compute_jacobian(lk, ln, d) ** 2 * k)


# --------------------------------------------------------------------------------------


def _eq_to_find_0(d, ln, lk, n, k):
    return compute_discrete_volume(ln, d) / compute_discrete_volume(lk, d) - n / k


# --------------------------------------------------------------------------------------


def find_d_root(ln, lk, n, k):
    if (
        n < 0.00001
    ):  # i.e. i'm dealing with a isolated points, there's no statistics on n
        return 0
    #    if abs(k-n)<0.00001: #i.e. there's internal and external shell have the same amount of points
    #        return 0
    return scipy.optimize.root_scalar(
        _eq_to_find_0,
        args=(ln, lk, n, k),
        bracket=(D_MIN + np.finfo(np.float16).eps, D_MAX),
    ).root


# --------------------------------------------------------------------------------------


def find_d_likelihood(ln, lk, n, k, ww):
    if isinstance(n, np.ndarray):
        n_check = n.mean()
    else:
        n_check = n
    if (
        n_check < 0.00001
    ):  # i.e. i'm dealing with isolated points, there's no statistics on n
        return 0
    #    if abs(k-n)<0.00001: #i.e. there's internal and external shell have the same amount of points
    #        return 0
    return SMin(
        _compute_binomial_logl,
        args=(lk, k, ln, n, True, ww),
        bounds=(D_MIN + np.finfo(np.float16).eps, D_MAX),
        method="bounded",
    ).x


# --------------------------------------------------------------------------------------
def beta_prior_d(k, n, lk, ln, a0=1, b0=1, plot=True, verbose=True):
    """Compute the posterior distribution of d given the input aggregates
    Since the likelihood is given by a binomial distribution, its conjugate prior is a beta distribution.
    However, the binomial is defined on the ratio of volumes and so do the beta distribution. As a
    consequence one has to change variable to have the distribution over d

    Args:
            k (nd.array(int)): number of points within the external shells
            n (nd.array(int)): number of points within the internal shells
            lk (int): outer shell radius
            ln (int): inner shell radius
            a0 (float): beta distribution parameter, default =1 for flat prior
            b0 (float): prior initializer, default =1 for flat prior
            plot (bool, default=False): plot the posterior
    Returns:
            E_d_emp (float): mean value of the posterior
            S_d_emp (float): std of the posterior
            d_range (ndarray(float)): domain of the posterior
            P (ndarray(float)): probability of the posterior
    """
    # from scipy.special import beta as beta_f
    from scipy.stats import beta as beta_d

    a = a0 + n.sum()
    b = b0 + k.sum() - n.sum()
    posterior = beta_d(a, b)

    def p_d(d):
        p = compute_discrete_volume(ln, d) / compute_discrete_volume(lk, d)
        dp_dd = _compute_jacobian(lk, ln, d)
        return abs(posterior.pdf(p) * dp_dd)

    # in principle we don't know where the distribution is peaked, so
    # we perform a blind search over the domain
    dx = 1.0
    d_left = D_MIN
    d_right = D_MAX + dx + d_left
    d_range = np.arange(d_left, d_right, dx)
    P = np.array([p_d(di) for di in d_range]) * dx
    counter = 0
    mask = P != 0
    elements = mask.sum()
    # if less than 3 points !=0 are found, reduce the interval
    while elements < 3:
        dx /= 10
        d_range = np.arange(d_left, d_right, dx)
        P = np.array([p_d(di) for di in d_range]) * dx
        mask = P != 0
        elements = mask.sum()
        counter += 1

    # with more than 3 points !=0 we can restrict the domain and have a smooth distribution
    # I choose 1000 points but such quantity can be varied according to necessity
    ind = np.where(mask)[0]
    d_left = d_range[ind[0]] - 0.5 * dx if d_range[ind[0]] - dx > 0 else D_MIN
    d_right = d_range[ind[-1]] + 0.5 * dx
    d_range = np.linspace(d_left, d_right, 1000)
    dx = d_range[1] - d_range[0]
    P = np.array([p_d(di) for di in d_range]) * dx
    P = P.reshape(P.shape[0])

    #    if verbose:
    #        print("iter no\t", counter,'\nd_left\t', d_left,'\nd_right\t', d_right, elements)

    if plot:
        plt.figure()
        plt.plot(d_range, P)
        plt.xlabel("d")
        plt.ylabel("P(d)")
        plt.title("posterior of d")
        plt.show()

    E_d_emp = np.dot(d_range, P)
    S_d_emp = np.sqrt((d_range * d_range * P).sum() - E_d_emp * E_d_emp)
    if plot:
        print("empirical average:\t", E_d_emp, "\nempirical std:\t\t", S_d_emp)
    #   theoretical results, valid only in the continuum case
    #   E_d = ( sp.digamma(a) - sp.digamma(a+b) )/np.log(r)
    #   S_d = np.sqrt( ( sp.polygamma(1,a) - sp.polygamma(1,a+b) )/np.log(r)**2 )

    return E_d_emp, S_d_emp, d_range, P


# --------------------------------------------------------------------------------------


def return_condensed_distances(
    points, metric="manhattan", d_max=100, maxk_ind=None, period=None
):

    dist_count = np.zeros((points.shape[0], d_max + 1), dtype=int)

    if maxk_ind is not None:
        indexes = np.zeros((points.shape[0], maxk_ind), dtype=int)

    if metric == "manhattan":
        return manhattan_distances(points, d_max, maxk_ind, period)
    elif metric == "hamming":
        return hamming_distances(points, d_max, maxk_ind)
    else:
        print(
            'insert a proper metric: up to now the supported ones are "manhattan" and "hamming"'
        )
        return 0


# --------------------------------------------------------------------------------------


def manhattan_distances(points, d_max=100, maxk_ind=None, period=None):

    dist_count = np.zeros((points.shape[0], d_max + 1), dtype=int)

    if maxk_ind is not None:
        indexes = np.zeros((points.shape[0], maxk_ind), dtype=int)

    for i, pt in enumerate(points):

        if period is None:
            appo = np.sum(abs(pt - points), axis=1, dtype=int)
        else:
            appo = pt - points
            appo = np.sum(
                abs(appo - np.rint(appo / period) * period), axis=1, dtype=int
            )

        if maxk_ind is None:
            uniq, counts = np.unique(appo, return_counts=True)
        else:
            index_i = np.argsort(appo)
            indexes[i] = np.copy(index_i[:maxk_ind])
            uniq, counts = np.unique(appo[index_i], return_counts=True)

        assert uniq[-1] <= d_max
        dist_count[i, uniq] = np.copy(counts)
        dist_count[i] = np.cumsum(dist_count[i])

    if maxk_ind is None:
        return dist_count, None
    else:
        return dist_count, indexes


# --------------------------------------------------------------------------------------


def hamming_distance_couple(a, b):
    # assert len(a)==len(b)
    return sum([a[i] != b[i] for i in range(len(a))])


# --------------------------------------------------------------------------------------


def hamming_distances(points, d_max=100, maxk_ind=None):

    dist_count = np.zeros((points.shape[0], d_max + 1), dtype=int)

    if maxk_ind is not None:
        indexes = np.zeros((points.shape[0], maxk_ind), dtype=int)

    for i, pt in enumerate(points):

        appo = np.array([hamming_distance_couple(pt, pt_i) for pt_i in points])

        if maxk_ind is None:
            uniq, counts = np.unique(appo, return_counts=True)
        else:
            index_i = np.argsort(appo)
            indexes[i] = np.copy(index_i[:maxk_ind])
            uniq, counts = np.unique(appo[index_i], return_counts=True)

        assert uniq[-1] <= d_max
        dist_count[i, uniq] = np.copy(counts)
        dist_count[i] = np.cumsum(dist_count[i])

    if maxk_ind is None:
        return dist_count, _
    else:
        return dist_count, indexes


# --------------------------------------------------------------------------------------
# ------------------------------PLOT ROUTINES-------------------------------------------


def plot_pdf(n_emp, n_mod, title, fileout=None):
    plt.figure()
    plt.title(title)
    plt.plot(n_emp, "-", label="n empirical", linewidth=4, alpha=0.9, c=cmap(9))
    plt.plot(n_mod, "--", label="n model", linewidth=4, alpha=0.9, c=cmap(0))
    plt.xlabel("n", fontsize=15)
    plt.ylabel("P(n)", fontsize=15)
    plt.legend(fontsize=14, frameon=False)
    plt.xticks(size=14)
    plt.yticks(size=14)
    plt.tight_layout()
    if fileout is not None:
        plt.savefig(fileout)
    else:
        plt.show()


# --------------------------------------------------------------------------------------


def plot_cdf(n_emp, n_mod, title, fileout=None):
    plt.figure()
    plt.title(title)
    plt.plot(n_emp, "-", label="n empirical", linewidth=4, alpha=0.9, c=cmap(9))
    plt.plot(n_mod, "--", label="n model", linewidth=4, alpha=0.9, c=cmap(0))
    plt.xlabel("n", fontsize=15)
    plt.ylabel("F(n)", fontsize=15)
    plt.legend(fontsize=14, frameon=False)
    plt.xticks(size=14)
    plt.yticks(size=14)
    plt.tight_layout()
    if fileout is not None:
        plt.savefig(fileout)
    else:
        plt.show()


# --------------------------------------------------------------------------------------


def plot_id_pv(x, idd, pv, title, xlabel, fileout):
    fig, ax1 = plt.subplots()

    c_left = "firebrick"
    c_right = "navy"

    plt.yticks(fontsize=13)
    plt.xticks(fontsize=13)

    ax1.set_title(title)
    ax1.set_xlabel(xlabel, size=15)
    ax1.set_ylabel("estimated id", size=15, c=c_left)
    ax1.tick_params(axis="y", colors=c_left)

    ax1.scatter(x, idd, alpha=0.85, c=c_left, s=75)

    ax2 = ax1.twinx()
    ax2.set_ylabel("p-value", size=15, c=c_right)
    ax2.tick_params(axis="y", colors=c_right)
    ax2.set_yscale("log")

    ax2.scatter(x, pv, marker="^", color=c_right, s=75)
    # ax2.plot(data[:,0],np.ones_like(data[:,-1])*0.05,'k--',alpha=0.5,label=r'$\alpha=0.05$')

    # plt.legend()
    plt.tight_layout()

    if fileout is not None:
        plt.savefig(fileout)
    else:
        plt.show()
