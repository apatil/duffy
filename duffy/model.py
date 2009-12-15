# Author: Anand Patil
# Date: 6 Feb 2009
# License: Creative Commons BY-NC-SA
####################################

# The idea: There are two mutations involved, one (Nr33) converting Fya to Fyb and one (Nr125) 
# silencing expression. The latter mutation tends to occur only with the former, but the converse 
# is not true.
# 
# The model for an individual chromosomal genotype is that the first mutation is a spatially-
# correlated random field, but the second occurs independently with some probability in Africa
# and another probability outside Africa.
# 
# There are two types of datapoints: 
#     - one testing individuals for phenotype (a-b-), meaning both chromosomes have the silencing
#       mutation.
#     - one testing individuals for expression of Fya on either chromosome.
# It's easy to make a likelihood model for either of these, just a bit complicated.
# The maps we'll eventually want to make will be of (a-b-) frequency, meaining the postprocessing
# function will need to close on model variables. The generic package doesn't currently support this.


import numpy as np
import pymc as pm
import gc
from map_utils import *
from generic_mbg import *
import generic_mbg

__all__ = ['make_model']

def ibd_covariance_submodel(suffix):
    """
    A small function that creates the mean and covariance object
    of the random field.
    """
    
    # from duffy import cut_matern
    
    # The partial sill.
    amp = pm.Exponential('amp_%s'%suffix, .1, value=1.)
    
    # The range parameter. Units are RADIANS. 
    # 1 radian = the radius of the earth, about 6378.1 km
    # scale = pm.Exponential('scale', 1./.08, value=.08)
    
    scale_shift = pm.Exponential('scale_shift_%s'%suffix, .1, value=.08)
    scale = pm.Lambda('scale_%s'%suffix,lambda s=scale_shift: s+.01)
    scale_in_km = scale*6378.1
    
    # This parameter controls the degree of differentiability of the field.
    diff_degree = pm.Uniform('diff_degree_%s'%suffix, .5, 3)
    
    # The nugget variance. Lower-bounded to preserve mixing.
    V = pm.Exponential('V_%s'%suffix, .1, value=1.)
    @pm.potential
    def V_bound(V=V):
        if V<.1:
            return -np.inf
        else:
            return 0
    
    
    # Create the covariance & its evaluation at the data locations.
    @pm.deterministic(trace=True)
    def C(amp=amp, scale=scale, diff_degree=diff_degree):
        """A covariance function created from the current parameter values."""
        return pm.gp.FullRankCovariance(pm.gp.matern.geo_rad, amp=amp, scale=scale, diff_degree=diff_degree)
    C.__name__ = 'C_%s'%suffix
    
    return locals()
        
# =========================
# = Haplotype frequencies =
# =========================
h_freqs = {'a': lambda pb, p0, p1: (1-pb)*(1-p1),
            'b': lambda pb, p0, p1: pb*(1-p0),
            '0': lambda pb, p0, p1: pb*p0,
            '1': lambda pb, p0, p1: (1-pb)*p1}
hfk = ['a','b','0','1']
hfv = [h_freqs[key] for key in hfk]

# ========================
# = Genotype frequencies =
# ========================
g_freqs = {}
for i in xrange(4):
    for j in xrange(i,4):
        if i != j:
            g_freqs[hfk[i]+hfk[j]] = lambda pb, p0, p1, i=i, j=j: 2 * np.asscalar(hfv[i](pb,p0,p1) * hfv[j](pb,p0,p1))
        else:
            g_freqs[hfk[i]*2] = lambda pb, p0, p1, i=i: np.asscalar(hfv[i](pb,p0,p1))**2
            
for i in xrange(1000):
    pb,p0,p1 = np.random.random(size=3)
    np.testing.assert_almost_equal(np.sum([gfi(pb,p0,p1) for gfi in g_freqs.values()]),1.)
    
def make_model(lon,lat,covariate_values,n,datatype,
                genaa,genab,genbb,gen00,gena0,genb0,gena1,genb1,gen01,gen11,
                pheab,phea,pheb,
                phe0,prom0,promab,
                aphea,aphe0,
                bpheb,bphe0,
                cpus=1):
    """
    This function is required by the generic MBG code.
    """
    # Step method granularity    
    grainsize = 1
        
    # Non-unique data locations
    data_mesh = combine_spatial_inputs(lon, lat)
    
    # Uniquify the data locations.
    locs = [(lon[0], lat[0])]
    fi = [0]
    ui = [0]
    for i in xrange(1,len(lon)):
        
        # If repeat location, add observation
        loc = (lon[i], lat[i])
        if loc in locs:
            fi.append(locs.index(loc))
            
        # Otherwise, new obs
        else:
            locs.append(loc)
            fi.append(max(fi)+1)
            ui.append(i)
    fi = np.array(fi)
    ti = [np.where(fi == i)[0] for i in xrange(max(fi)+1)]
    ui = np.asarray(ui)
    
    lon = np.array(locs)[:,0]
    lat = np.array(locs)[:,1]
    
    # Unique data locations
    logp_mesh = combine_spatial_inputs(lon,lat)
    
    # Create the mean & its evaluation at the data locations.
    print data_mesh.shape
    init_OK = False
    
    # Probability of mutation in the promoter region, given that the other thing is a.
    p1 = pm.Uniform('p1', 0, .04, value=.01)
    
    while not init_OK:
        try:        
            
            # Mean functions of the random fields controlling a/b switch and promoter given b frequencies.
            M_b, M_eval_b = trivial_means(logp_mesh)
            M_b.__name__ = 'M_b'
            M_eval_b.__name__ = 'M_eval_b'
            M_0, M_eval_0 = trivial_means(logp_mesh)
            M_0.__name__ = 'M_0'
            M_eval_0.__name__ = 'M_eval_0'
            
            # Covariance functions.
            sp_sub_b = ibd_covariance_submodel('b')    
            sp_sub_0 = ibd_covariance_submodel('0')
            covariate_dict_b, C_eval_b = cd_and_C_eval(covariate_values, sp_sub_b['C'], data_mesh, ui)
            C_eval_b.__name__ = 'C_eval_b'
            covariate_dict_0, C_eval_0 = cd_and_C_eval({}, sp_sub_0['C'], data_mesh, ui)
            C_eval_0.__name__ = 'C_eval_0'
        
            # The fields evaluated at the uniquified data locations            
            fb = pm.MvNormalCov('fb', M_eval_b, C_eval_b)
            f0 = pm.MvNormalCov('f0', M_eval_0, C_eval_0)

            # Make the fs start somewhere a bit sane
            fb.value = fb.value - np.mean(fb.value)
            f0.value = f0.value - np.mean(f0.value)            
            
            # Loop over data clusters, adding nugget and applying link function.
            eps_p_f0_d = []
            p0_d = []
            eps_p_fb_d = []
            pb_d = []
        
            tau_b = 1./sp_sub_b['V']
            tau_0 = 1./sp_sub_0['V']            
        
            for i in xrange(len(n)/grainsize+1):
                sl = slice(i*grainsize,(i+1)*grainsize,None)
                
                if sl.stop<len(n)+1:
                    this_fb = pm.Lambda('fb_%i'%i, lambda f=fb, sl=sl, fi=fi: f[fi[sl]], trace=False)
                    this_f0 = pm.Lambda('f0_%i'%i, lambda f=f0, sl=sl, fi=fi: f[fi[sl]], trace=False)

                    # Nuggeted field in this cluster
                    eps_p_fb_d.append(pm.Normal('eps_p_fb_%i'%i, this_fb, tau_b, value=np.random.normal(), trace=False))
                    eps_p_f0_d.append(pm.Normal('eps_p_f0_%i'%i, this_f0, tau_0, value=np.random.normal(), trace=False))
                
                    # The allele frequency
                    pb_d.append(pm.Lambda('pb_%i'%i,lambda lt=eps_p_fb_d[-1]: invlogit(np.atleast_1d(lt)),trace=False))
                    p0_d.append(pm.Lambda('p0_%i'%i,lambda lt=eps_p_f0_d[-1]: invlogit(np.atleast_1d(lt)),trace=False))
        
            # The fields plus the nugget
            @pm.deterministic
            def eps_p_fb(eps_p_fb_d = eps_p_fb_d):
                """Concatenated version of eps_p_fb, for postprocessing & Gibbs sampling purposes"""
                return np.hstack(eps_p_fb_d)

            @pm.deterministic
            def eps_p_f0(eps_p_f0_d = eps_p_f0_d):
                """Concatenated version of eps_p_f0, for postprocessing & Gibbs sampling purposes"""
                return np.hstack(eps_p_f0_d)
        
            init_OK = True
        except pm.ZeroProbability, msg:
            print 'Trying again: %s'%msg
            init_OK = False
            gc.collect()

    # The likelihoods.
    data_d = []    
    for i in xrange(len(n)):

        sl_ind = int(i/grainsize)
        sub_ind = i%grainsize
        
        # See duffy/doc/model.tex for explanations of the likelihoods.
        p0 = pm.Lambda('p0_%i_%i'%(sl_ind,sub_ind), lambda p=p0_d[sl_ind], j=sub_ind: p[j], trace=False)
        pb = pm.Lambda('pb_%i_%i'%(sl_ind,sub_ind), lambda p=pb_d[sl_ind], j=sub_ind: p[j], trace=False)
        
        if datatype[i]=='prom':
            cur_obs = [prom0[i], promab[i]]
            # Need to have either b and 0 or a and 1 on both chromosomes
            p = pm.Lambda('p_%i'%i, lambda pb=pb, p0=p0, p1=p1: (pb*p0+(1-pb)*p1)**2, trace=False)
            n = np.sum(cur_obs)
            data_d.append(pm.Binomial('data_%i'%i, p=p, n=n, value=prom0[i], observed=True))
            
        elif datatype[i]=='aphe':
            cur_obs = [aphea[i], aphe0[i]]
            n = np.sum(cur_obs)
            # Need to have (a and not 1) on either chromosome, or not (not (a and not 1) on both chromosomes)
            p = pm.Lambda('p_%i'%i, lambda pb=pb, p0=p0, p1=p1: 1-(1-(1-pb)*(1-p1))**2, trace=False)
            data_d.append(pm.Binomial('data_%i'%i, p=p, n=n, value=aphea[i], observed=True))
            
        elif datatype[i]=='bphe':
            cur_obs = [bpheb[i], bphe0[i]]
            n = np.sum(cur_obs)
            # Need to have (b and not 0) on either chromosome
            p = pm.Lambda('p_%i'%i, lambda pb=pb, p0=p0, p1=p1: 1-(1-pb*(1-p0))**2, trace=False)
            data_d.append(pm.Binomial('data_%i'%i, p=p, n=n, value=aphea[i], observed=True))            
            
        elif datatype[i]=='phe':
            cur_obs = np.array([pheab[i],phea[i],pheb[i],phe0[i]])
            n = np.sum(cur_obs)
            p = pm.Lambda('p_%i'%i, lambda pb=pb, p0=p0, p1=p1: np.array([\
                g_freqs['ab'](pb,p0,p1),
                g_freqs['a0'](pb,p0,p1)+g_freqs['a1'](pb,p0,p1)+g_freqs['aa'](pb,p0,p1),
                g_freqs['b0'](pb,p0,p1)+g_freqs['b1'](pb,p0,p1)+g_freqs['bb'](pb,p0,p1),
                g_freqs['00'](pb,p0,p1)+g_freqs['01'](pb,p0,p1)+g_freqs['11'](pb,p0,p1)]), trace=False)
            np.testing.assert_almost_equal(p.value.sum(), 1)
            data_d.append(pm.Multinomial('data_%i'%i, p=p, n=n, value=cur_obs, observed=True))
            
        elif datatype[i]=='gen':
            cur_obs = np.array([genaa[i],genab[i],gena0[i],gena1[i],genbb[i],genb0[i],genb1[i],gen00[i],gen01[i],gen11[i]])
            n = np.sum(cur_obs)
            p = pm.Lambda('p_%i'%i, lambda pb=pb, p0=p0, p1=p1, g_freqs=g_freqs: \
                np.array([g_freqs[key](pb,p0,p1) for key in ['aa','ab','a0','a1','bb','b0','b1','00','01','11']]), trace=False)
            np.testing.assert_almost_equal(p.value.sum(), 1)
            data_d.append(pm.Multinomial('data_%i'%i, p=p, n=n, value=cur_obs, observed=True))
            
        if np.any(np.isnan(cur_obs)):
            raise ValueError
    
    covariate_dicts = {'eps_p_fb': covariate_dict_b, 'eps_p_f0': covariate_dict_0}
            
    out = locals()
    # out.pop('sp_sub_b')
    # out.pop('sp_sub_0')
    for d in sp_sub_b.values() + sp_sub_0.values():
        if isinstance(d,pm.Variable):
            out[d.__name__] = d

    return out