// Copyright (c) 2026 OceanBase.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

#include <boost/numeric/conversion/cast.hpp>
#include <cassert>
#include <limits>
#include <type_traits>

/** Verify enum wrapper values and numeric conversion overflow behavior. */
int main()
{
  using namespace boost::numeric;
  static_assert(convdetail::float2float_c::value == float_to_float);
  static_assert(convdetail::udt2udt_c::value == udt_to_udt);
  static_assert(convdetail::unsig2sig_c::value == unsigned_to_signed);
  static_assert(std::is_same<convdetail::float2float_c::value_type,
                            int_float_mixture_enum>::value);
  assert(boost::numeric_cast<int>(1.75) == 1);
  assert(boost::numeric_cast<double>(42) == 42.0);
  bool overflow = false;
  try {
    (void)boost::numeric_cast<unsigned char>(256);
  } catch (const boost::numeric::bad_numeric_cast &) {
    overflow = true;
  }
  assert(overflow);
  return 0;
}
