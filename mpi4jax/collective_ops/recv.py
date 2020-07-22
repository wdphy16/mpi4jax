import numpy as _np

from mpi4py import MPI as _MPI

from jax import abstract_arrays
from jax.core import Primitive
from jax.lib import xla_client
from jax.interpreters import xla, ad, batching

from ..utils import (
    to_mpi_ptr,
    _unpack_builder,
    _ops,
    _constant_s32_scalar,
    _constant_u64_scalar,
    dtype_ptr,
)

# The Jax primitive
mpi_recv_p = Primitive("recv_mpi")  # Create the primitive

# This function applies the primitive to an AST


def Recv(x, source=_MPI.ANY_SOURCE, tag=_MPI.ANY_TAG, comm=_MPI.COMM_WORLD,
         status=None):
    return mpi_recv_p.bind(x, source=source, tag=tag, comm=comm, status=status)


#  this function executes the primitive, when not under any transformation
def mpi_recv_impl(x, source, tag, comm, status):
    # TODO: make this support gpus (use cupy?)
    inpt = _np.empty_like(x)
    comm.Recv(inpt, source=source, tag=tag, status=status)
    return inpt


#  This function compiles the operation
def mpi_recv_xla_encode(c, x, source, tag, comm, status):
    c = _unpack_builder(c)
    x_shape = c.GetShape(x)
    dtype = x_shape.element_type()
    dims = x_shape.dimensions()

    # compute total number of elements in array
    nitems = dims[0]
    for el in dims[1:]:
        nitems *= el

    _nitems = _constant_s32_scalar(c, nitems)

    _dtype_ptr = dtype_ptr(dtype)

    sh = xla_client.Shape.array_shape(dtype, dims)

    if status is None:
        return _ops.CustomCall(
            c,
            b"mpi_recv_ignore_status",
            operands=(
                _nitems,
                x,
                _constant_s32_scalar(c, source),
                _constant_s32_scalar(c, tag),
                _constant_u64_scalar(c, to_mpi_ptr(comm)),
                _constant_u64_scalar(c, _dtype_ptr),
            ),
            shape=sh,
        )

    return _ops.CustomCall(
        c,
        b"mpi_recv",
        operands=(
            _nitems,
            x,
            _constant_s32_scalar(c, source),
            _constant_s32_scalar(c, tag),
            _constant_u64_scalar(c, to_mpi_ptr(comm)),
            _constant_u64_scalar(c, _dtype_ptr),
            _constant_u64_scalar(c, to_mpi_ptr(status)),
        ),
        shape=sh,
    )


# This function evaluates only the shapes during AST construction
def mpi_recv_abstract_eval(xs, op, comm):
    return abstract_arrays.ShapedArray(xs.shape, xs.dtype)


# This function binds the batched transformation.
def mpi_recv_batching(in_args, batch_axes, **kwargs):
    (x,) = in_args
    res = Recv(x, **kwargs)
    return res, batch_axes[0]


def mpi_recv_value_and_jvp(in_args, tan_args, op, **kwargs):
    (x,) = in_args
    res = Recv(x, op=op, **kwargs)

    # Identify the correct adjoint
    if op == _MPI.SUM:
        (x_tan,) = tan_args
    else:
        raise NotImplementedError(
            "The adjoint of recv for {} operation is not defined".format(
                op)
        )

    jvp = x_tan
    return (res, jvp)


mpi_recv_p.def_impl(mpi_recv_impl)
mpi_recv_p.def_abstract_eval(mpi_recv_abstract_eval)

batching.primitive_batchers[mpi_recv_p] = mpi_recv_batching
ad.primitive_jvps[mpi_recv_p] = mpi_recv_value_and_jvp

# assign to the primitive the correct encoder
xla.backend_specific_translations["cpu"][mpi_recv_p] = mpi_recv_xla_encode
