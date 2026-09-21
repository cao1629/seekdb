#include <unicode/uregex.h>
#include <cstdio>

/** Verify Unicode property data and matching in the statically linked ICU. */
int main()
{
  const UChar pattern[] = {0x5c, 'p', '{', 'H', 'a', 'n', '}', '+', 0};
  const UChar text[] = {'a', 0x4e2d, 0x6587, 'b', 0};
  UErrorCode status = U_ZERO_ERROR;
  UParseError parse_error = {};
  URegularExpression *regex = uregex_open(pattern, -1, 0, &parse_error, &status);
  if (U_FAILURE(status) || regex == nullptr) return 1;
  uregex_setText(regex, text, -1, &status);
  const bool found = uregex_find(regex, 0, &status);
  const int start = uregex_start(regex, 0, &status);
  const int end = uregex_end(regex, 0, &status);
  uregex_close(regex);
  if (U_FAILURE(status) || !found || start != 1 || end != 3) return 2;
  std::puts("ICU Unicode Han property and regex checks passed");
  return 0;
}
