import numpy as np
from sympy import sqrt

from devito import (Grid, Function, TimeFunction, Eq, Operator, norm)
from examples.seismic import RickerSource, TimeAxis

space_order = 8
dtype = np.float32
npad = 20
qmin = 0.1
qmax = 1000.0
tmax = 250.0
fpeak = 0.010
omega = 2.0 * np.pi * fpeak

shape = (1201, 1201, 601)
spacing = (10.0, 10.0, 10.0)
origin = tuple([0.0 for s in shape])
extent = tuple([d * (s - 1) for s, d in zip(shape, spacing)])
grid = Grid(extent=extent, shape=shape, origin=origin, dtype=dtype)

b = Function(name='b', grid=grid, space_order=space_order)
f = Function(name='f', grid=grid, space_order=space_order)
vel = Function(name='vel', grid=grid, space_order=space_order)
eps = Function(name='eps', grid=vel.grid, space_order=space_order)
eta = Function(name='eta', grid=vel.grid, space_order=space_order)
wOverQ = Function(name='wOverQ', grid=vel.grid, space_order=space_order)

b.data[:] = 1.0
f.data[:] = 0.84
vel.data[:] = 1.5
eps.data[:] = 0.2
eta.data[:] = 0.4
wOverQ.data[:] = 1.0

t0 = 0.0
t1 = 250.0
dt = 1.0
time_axis = TimeAxis(start=t0, stop=t1, step=dt)

p0 = TimeFunction(name='p0', grid=grid, time_order=2, space_order=space_order)
m0 = TimeFunction(name='m0', grid=grid, time_order=2, space_order=space_order)
t, x, y, z = p0.dimensions

src_coords = np.empty((1, len(shape)), dtype=dtype)
# src_coords[0, :] = [d * (s-1)//2 for d, s in zip(spacing, shape)]
src_coords[0, :] = [d * (s-1)//2 + 100 for d, s in zip(spacing, shape)]
src = RickerSource(name='src', grid=vel.grid, f0=fpeak, npoint=1, time_range=time_axis)
src.coordinates.data[:] = src_coords[:]
src_term = src.inject(field=p0.forward, expr=src * t.spacing**2 * vel**2 / b)


def g1(field):
    return field.dx(x0=x+x.spacing/2)


def g2(field):
    return field.dy(x0=y+y.spacing/2)


def g3(field):
    return field.dz(x0=z+z.spacing/2)


def g1_tilde(field):
    return field.dx(x0=x-x.spacing/2)


def g2_tilde(field):
    return field.dy(x0=y-y.spacing/2)


def g3_tilde(field):
    return field.dz(x0=z-z.spacing/2)


# Functions  for additional factorization
b1mf = Function(name='b1mf', grid=grid, space_order=space_order)
b1p2e = Function(name='b1p2e', grid=grid, space_order=space_order)
b1mfa2 = Function(name='b1mfa2', grid=grid, space_order=space_order)
b1mfpfa2 = Function(name='b1mfpfa2', grid=grid, space_order=space_order)
bfes1ma2 = Function(name='bfes1ma2', grid=grid, space_order=space_order)

p_x = Function(name='p_x', grid=grid, space_order=space_order)
p_y = Function(name='p_y', grid=grid, space_order=space_order)
p_z = Function(name='p_z', grid=grid, space_order=space_order)
m_x = Function(name='m_x', grid=grid, space_order=space_order)
m_y = Function(name='m_y', grid=grid, space_order=space_order)
m_z = Function(name='m_z', grid=grid, space_order=space_order)

# Equations for additional factorization
eq_b1mf = Eq(b1mf, b * (1 - f))
eq_b1p2e = Eq(b1p2e, b * (1 + 2 * eps))
eq_b1mfa2 = Eq(b1mfa2, b * (1 - f * eta**2))
eq_b1mfpfa2 = Eq(b1mfpfa2, b * (1 - f + f * eta**2))
eq_bfes1ma2 = Eq(bfes1ma2, b * f * eta * sqrt(1 - eta**2))

eq_px = Eq(p_x, g1_tilde(b1p2e * g1(p0)))
eq_py = Eq(p_y, g2_tilde(b1p2e * g2(p0)))
eq_pz = Eq(p_z, g3_tilde(b1mfa2 * g3(p0) + bfes1ma2 * g3(m0)))

eq_mx = Eq(m_x, g1_tilde(b1mf * g1(m0)))
eq_my = Eq(m_y, g2_tilde(b1mf * g2(m0)))
eq_mz = Eq(m_z, g3_tilde(b1mfpfa2 * g3(m0) + bfes1ma2 * g3(p0)))

# Time update equation for quasi-P state variable p
update_p_nl = t.spacing**2 * vel**2 / b * (p_x + p_y + p_z) + \
    (2 - t.spacing * wOverQ) * p0 + (t.spacing * wOverQ - 1) * p0.backward

# Time update equation for quasi-S state variable m
update_m_nl = t.spacing**2 * vel**2 / b * (m_x + m_y + m_z) + \
    (2 - t.spacing * wOverQ) * m0 + (t.spacing * wOverQ - 1) * m0.backward

stencil_p_nl = Eq(p0.forward, update_p_nl)
stencil_m_nl = Eq(m0.forward, update_m_nl)

dt = time_axis.step
spacing_map = vel.grid.spacing_map
spacing_map.update({t.spacing: dt})


# Findings
#   advanced-fsg reduces flops and performance for all ops
#   {'blockinner': True} --> 3D cache blocking, no improvements
#   {'min-storage': False} --> 15% lift for SSA VTI factorized op


op = Operator([eq_b1mf, eq_b1p2e, eq_b1mfa2, eq_b1mfpfa2, eq_bfes1ma2,
               eq_px, eq_py, eq_pz, eq_mx, eq_my, eq_mz, 
               stencil_p_nl, stencil_m_nl, src_term],
              subs=spacing_map, name='operator.vti_fact2.advanced.c')
f = open("operator.vti_fact2.advanced.c", "w")
print(op, file=f)
f.close()


op = Operator([eq_b1mf, eq_b1p2e, eq_b1mfa2, eq_b1mfpfa2, eq_bfes1ma2,
               eq_px, eq_py, eq_pz, eq_mx, eq_my, eq_mz, 
               stencil_p_nl, stencil_m_nl, src_term],
              subs=spacing_map, name='operator.vti_fact2.advanced-fsg.c',
              opt=('advanced-fsg'))
f = open("operator.vti_fact2.advanced-fsg.c", "w")
print(op, file=f)
f.close()


op = Operator([eq_b1mf, eq_b1p2e, eq_b1mfa2, eq_b1mfpfa2, eq_bfes1ma2,
               eq_px, eq_py, eq_pz, eq_mx, eq_my, eq_mz, 
               stencil_p_nl, stencil_m_nl, src_term],
              subs=spacing_map, name='operator.vti_fact2.advanced-blockinner.c',
              opt=('advanced', {'blockinner': True}))
f = open("operator.vti_fact2.advanced-blockinner.c", "w")
print(op, file=f)
f.close()


op = Operator([eq_b1mf, eq_b1p2e, eq_b1mfa2, eq_b1mfpfa2, eq_bfes1ma2,
               eq_px, eq_py, eq_pz, eq_mx, eq_my, eq_mz, 
               stencil_p_nl, stencil_m_nl, src_term],
              subs=spacing_map, name='operator.vti_fact2.advanced-fsg-blockinner.c',
              opt=('advanced-fsg', {'blockinner': True}))
f = open("operator.vti_fact2.advanced-fsg-blockinner.c", "w")
print(op, file=f)
f.close()


op = Operator([eq_b1mf, eq_b1p2e, eq_b1mfa2, eq_b1mfpfa2, eq_bfes1ma2,
               eq_px, eq_py, eq_pz, eq_mx, eq_my, eq_mz, 
               stencil_p_nl, stencil_m_nl, src_term],
              subs=spacing_map, name='operator.vti_fact2.advanced-cire-mincost-sops0001.c',
              opt=('advanced', {'cire-mincost-sops': 1}))
f = open("operator.vti_fact2.advanced-cire-mincost-sops0001.c", "w")
print(op, file=f)
f.close()


op = Operator([eq_b1mf, eq_b1p2e, eq_b1mfa2, eq_b1mfpfa2, eq_bfes1ma2,
               eq_px, eq_py, eq_pz, eq_mx, eq_my, eq_mz, 
               stencil_p_nl, stencil_m_nl, src_term],
              subs=spacing_map, name='operator.vti_fact2.advanced-cire-mincost-sops1000.c',
              opt=('advanced', {'cire-mincost-sops': 1000}))
f = open("operator.vti_fact2.advanced-cire-mincost-sops1000.c", "w")
print(op, file=f)
f.close()


op = Operator([eq_b1mf, eq_b1p2e, eq_b1mfa2, eq_b1mfpfa2, eq_bfes1ma2,
               eq_px, eq_py, eq_pz, eq_mx, eq_my, eq_mz, 
               stencil_p_nl, stencil_m_nl, src_term],
              subs=spacing_map, name='operator.vti_fact2.advanced-min-storage0.c',
              opt=('advanced', {'min-storage': False}))
f = open("operator.vti_fact2.advanced-min-storage0.c", "w")
print(op, file=f)
f.close()


op = Operator([eq_b1mf, eq_b1p2e, eq_b1mfa2, eq_b1mfpfa2, eq_bfes1ma2,
               eq_px, eq_py, eq_pz, eq_mx, eq_my, eq_mz, 
               stencil_p_nl, stencil_m_nl, src_term],
              subs=spacing_map, name='operator.vti_fact2.advanced-min-storage1.c',
              opt=('advanced', {'min-storage': True}))
f = open("operator.vti_fact2.c", "w")
f = open("operator.vti_fact2.advanced-min-storage1.c", "w")
print(op, file=f)
f.close()


op = Operator([eq_b1mf, eq_b1p2e, eq_b1mfa2, eq_b1mfpfa2, eq_bfes1ma2,
               eq_px, eq_py, eq_pz, eq_mx, eq_my, eq_mz, 
               stencil_p_nl, stencil_m_nl, src_term],
              subs=spacing_map, name='operator.vti_fact2.advanced-cire-repeats-inv0.c',
              opt=('advanced', {'cire-repeats-inv': 0}))
f = open("operator.vti_fact2.advanced-cire-repeats-inv0.c", "w")
print(op, file=f)
f.close()


op = Operator([eq_b1mf, eq_b1p2e, eq_b1mfa2, eq_b1mfpfa2, eq_bfes1ma2,
               eq_px, eq_py, eq_pz, eq_mx, eq_my, eq_mz, 
               stencil_p_nl, stencil_m_nl, src_term],
              subs=spacing_map, name='operator.vti_fact2.advanced-cire-repeats-inv1.c',
              opt=('advanced', {'cire-repeats-inv': 1}))
f = open("operator.vti_fact2.advanced-cire-repeats-inv1.c", "w")
print(op, file=f)
f.close()


op = Operator([eq_b1mf, eq_b1p2e, eq_b1mfa2, eq_b1mfpfa2, eq_bfes1ma2,
               eq_px, eq_py, eq_pz, eq_mx, eq_my, eq_mz, 
               stencil_p_nl, stencil_m_nl, src_term],
              subs=spacing_map, name='operator.vti_fact2.advanced-cire-repeats-inv2.c',
              opt=('advanced', {'cire-repeats-inv': 2}))
f = open("operator.vti_fact2.advanced-cire-repeats-inv2.c", "w")
print(op, file=f)
f.close()


op = Operator([eq_b1mf, eq_b1p2e, eq_b1mfa2, eq_b1mfpfa2, eq_bfes1ma2,
               eq_px, eq_py, eq_pz, eq_mx, eq_my, eq_mz, 
               stencil_p_nl, stencil_m_nl, src_term],
              subs=spacing_map, name='operator.vti_fact2.advanced-cire-repeats-inv2-cire-mincost-inv10.c',
              opt=('advanced', {'cire-repeats-inv': 2, 'cire-mincost-inv': 10}))
f = open("operator.vti_fact2.advanced-cire-repeats-inv2-cire-mincost-inv10.c", "w")
print(op, file=f)
f.close()


op = Operator([eq_b1mf, eq_b1p2e, eq_b1mfa2, eq_b1mfpfa2, eq_bfes1ma2,
               eq_px, eq_py, eq_pz, eq_mx, eq_my, eq_mz, 
               stencil_p_nl, stencil_m_nl, src_term],
              subs=spacing_map, name='operator.vti_fact2.advanced-cire-repeats-inv2-cire-mincost-inv20.c',
              opt=('advanced', {'cire-repeats-inv': 2, 'cire-mincost-inv': 20}))
f = open("operator.vti_fact2.advanced-cire-repeats-inv2-cire-mincost-inv20.c", "w")
print(op, file=f)
f.close()


op = Operator([eq_b1mf, eq_b1p2e, eq_b1mfa2, eq_b1mfpfa2, eq_bfes1ma2,
               eq_px, eq_py, eq_pz, eq_mx, eq_my, eq_mz, 
               stencil_p_nl, stencil_m_nl, src_term],
              subs=spacing_map, name='operator.vti_fact2.advanced-cire-repeats-inv2-cire-mincost-inv30.c',
              opt=('advanced', {'cire-repeats-inv': 2, 'cire-mincost-inv': 30}))
f = open("operator.vti_fact2.advanced-cire-repeats-inv2-cire-mincost-inv30.c", "w")
print(op, file=f)
f.close()


op = Operator([eq_b1mf, eq_b1p2e, eq_b1mfa2, eq_b1mfpfa2, eq_bfes1ma2,
               eq_px, eq_py, eq_pz, eq_mx, eq_my, eq_mz, 
               stencil_p_nl, stencil_m_nl, src_term],
              subs=spacing_map, name='operator.vti_fact2.advanced-cire-repeats-inv2-cire-mincost-inv40.c',
              opt=('advanced', {'cire-repeats-inv': 2, 'cire-mincost-inv': 40}))
f = open("operator.vti_fact2.advanced-cire-repeats-inv2-cire-mincost-inv40.c", "w")
print(op, file=f)
f.close()


op = Operator([eq_b1mf, eq_b1p2e, eq_b1mfa2, eq_b1mfpfa2, eq_bfes1ma2,
               eq_px, eq_py, eq_pz, eq_mx, eq_my, eq_mz, 
               stencil_p_nl, stencil_m_nl, src_term],
              subs=spacing_map, name='operator.vti_fact2.advanced-min-storage1-cire-mincost-sops1.c',
              opt=('advanced', {'min-storage': True, 'cire-mincost-sops': 1}))
f = open("operator.vti_fact2.advanced-min-storage1-cire-mincost-sops1.c", "w")
print(op, file=f)
f.close()