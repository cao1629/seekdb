#include <lapacke.h>
#include <math.h>
#include <stdio.h>

/** Check Accelerate-backed LAPACKE row-major operations used by VSAG. */
int main(void)
{
  float original[6] = {1, 2, 3, 4, 5, 7};
  float qr[6] = {1, 2, 3, 4, 5, 7};
  float tau[2];
  if (LAPACKE_sgeqrf(LAPACK_ROW_MAJOR, 3, 2, qr, 2, tau)) return 1;
  float r00 = qr[0], r01 = qr[1], r11 = qr[3];
  if (LAPACKE_sorgqr(LAPACK_ROW_MAJOR, 3, 2, 2, qr, 2, tau)) return 2;
  for (int row = 0; row < 3; ++row) {
    if (fabsf(qr[2 * row] * r00 - original[2 * row]) > 1e-4f) return 3;
    if (fabsf(qr[2 * row] * r01 + qr[2 * row + 1] * r11
              - original[2 * row + 1]) > 1e-4f) return 4;
  }
  float lu[4] = {4, 2, 1, 3};
  lapack_int pivots[2];
  if (LAPACKE_sgetrf(LAPACK_ROW_MAJOR, 2, 2, lu, 2, pivots)) return 5;
  if (pivots[0] != 1 || fabsf(lu[2] - 0.25f) > 1e-5f
      || fabsf(lu[3] - 2.5f) > 1e-5f) return 6;
  float symmetric[4] = {2, 1, 1, 2}, eigenvalues[2];
  if (LAPACKE_ssyev(LAPACK_ROW_MAJOR, 'V', 'U', 2, symmetric, 2, eigenvalues)) return 7;
  if (fabsf(eigenvalues[0] - 1) > 1e-5f || fabsf(eigenvalues[1] - 3) > 1e-5f) return 8;
  float a[6] = {3, 0, 0, 2, 0, 0}, singular[2], u[9], vt[4];
  if (LAPACKE_sgesdd(LAPACK_ROW_MAJOR, 'A', 3, 2, a, 2, singular, u, 3, vt, 2)) return 9;
  if (fabsf(singular[0] - 3) > 1e-5f || fabsf(singular[1] - 2) > 1e-5f) return 10;
  puts("LAPACKE QR, LU, eigenvalue and SVD checks passed");
  return 0;
}
