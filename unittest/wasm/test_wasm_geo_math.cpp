/*
 * Copyright (c) 2025 OceanBase.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#include "share/geo/geo_math.h"
#include <boost/geometry.hpp>
#include <boost/geometry/srs/transformation.hpp>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <limits>

namespace gm = oceanbase::common::geometry_math;
namespace bg = boost::geometry;

static bool same(double x, double y)
{
  uint64_t first = 0;
  uint64_t second = 0;
  std::memcpy(&first, &x, sizeof(first));
  std::memcpy(&second, &y, sizeof(second));
  return first == second || (std::isnan(x) && std::isnan(y));
}

static void check_primitives()
{
  const double pairs[][3] = {
    {0x1.1e9ce65793c00p-5, 0x1.7fb6725f75800p-4, 0x1.6e0287e015beep-2},
    {0x1.b2ad891f4696ap+17, 0x1.84d2b3f5cf3c2p+22, 0x1.1e1307a5246e5p-5},
    {-0x1.58a369f851cbbp-1, 0x1.9c74a08b089eap-2, -0x1.0814765de7354p+0},
    {-0x1.58a369f851cbbp-1, 0x1.9c74a08b089e9p-2, -0x1.0814765de7355p+0},
  };
  for (const auto &pair : pairs) { assert(gm::atan2(pair[0], pair[1]) == pair[2]); }
  assert(gm::cos(-0x1.7a1271e3e7529p-1) == 0x1.7aa3e59316a7dp-1);
  const double cases[] = {0.0, -0.0, 1.0, -1.0, INFINITY, -INFINITY, NAN,
      std::numeric_limits<double>::denorm_min(), -std::numeric_limits<double>::denorm_min(),
      std::numeric_limits<double>::max(), -std::numeric_limits<double>::max()};
  for (double y : cases) {
    for (double x : cases) {
      if (x == 0 || y == 0 || !std::isfinite(x) || !std::isfinite(y)) {
        assert(same(gm::atan2(y, x), std::atan2(y, x)));
      }
    }
    if (y == 0 || !std::isfinite(y)) { assert(same(gm::cos(y), std::cos(y))); }
  }
  static_assert(std::is_same_v<decltype(gm::atan2(1.0f, 2.0f)), float>);
  static_assert(std::is_same_v<decltype(gm::cos(1.0f)), float>);
  static_assert(std::is_same_v<decltype(gm::atan2(1.0L, 2.0L)), long double>);
  static_assert(std::is_same_v<decltype(gm::cos(1.0L)), long double>);
  static_assert(std::is_same_v<decltype(gm::atan2(1.0f, 2.0)), double>);
  static_assert(std::is_same_v<decltype(gm::atan2(1.0, 2.0L)), long double>);
  assert(gm::atan2(1.0f, 2.0f) == std::atan2(1.0f, 2.0f));
  assert(gm::cos(1.0f) == std::cos(1.0f));
  assert(gm::atan2(1.0L, 2.0L) == std::atan2(1.0L, 2.0L));
  assert(gm::cos(1.0L) == std::cos(1.0L));
  assert(gm::atan2(1.0f, 2.0) == std::atan2(1.0f, 2.0));
  assert(gm::atan2(1.0, 2.0L) == std::atan2(1.0, 2.0L));
}

static void check_buffer()
{
  using Point = bg::model::d2::point_xy<double>;
  using Line = bg::model::linestring<Point>;
  using Polygon = bg::model::polygon<Point, false, true>;
  using MultiPolygon = bg::model::multi_polygon<Polygon>;
  const double points[][2] = {
    {-116.93414544665981, 34.16033385105459},
    {-116.87777514700957, 34.10831080544884},
    {-116.86972224705954, 34.086748622072776},
    {-116.9327074288116, 34.08458099517253},
    {-117.00216369088065, 34.130329331330216},
    {-117.00216369088065, 34.130329331330216},
  };
  Line line;
  for (const auto &point : points) { line.emplace_back(point[0], point[1]); }
  MultiPolygon output;
  bg::strategy::buffer::distance_symmetric<double> distance(0.1);
  bg::strategy::buffer::side_straight side;
  bg::strategy::buffer::join_round join(32);
  bg::strategy::buffer::end_round end(32);
  bg::strategy::buffer::point_circle circle(32);
  bg::buffer(line, output, distance, side, join, end, circle);
  assert(output.size() == 1 && output[0].inners().empty());
  assert(output[0].outer().size() == 43);
  assert(output[0].outer()[24].y() == 0x1.103fce8095525p+5);
  assert(bg::area(output) == 0x1.1854f687e2dcep-4);
}

static void check_transform()
{
  using Geographic = bg::model::point<double, 2, bg::cs::geographic<bg::radian>>;
  using Cartesian = bg::model::point<double, 2, bg::cs::cartesian>;
  bg::srs::proj4 source("+proj=lonlat +a=6378137 +rf=298.257223563 +towgs84=0,0,0,0,0,0,0 +no_defs");
  bg::srs::proj4 target_geo("+proj=lonlat +a=6378388 +rf=297 +towgs84=-83.11,-97.38,-117.22,0.00569290865241987,-0.0446975835137458,0.0442850539012516,0.1218 +no_defs");
  bg::srs::proj4 target_cart("+proj=utm +zone=33 +ellps=WGS84 +towgs84=0,0,0,0,0,0,0 +units=m +no_defs");
  bg::srs::transformation<> geo_transform(source, target_geo);
  bg::srs::transformation<> cart_transform(source, target_cart);
  const double points[][4] = {
    {2, 2, 0x1.1e1307a5246e5p-5, 0x1.1e1bd67d01621p-5},
    {8, 2, 0x1.1dfb483b52f57p-3, 0x1.1e1bba32ecfdep-5},
    {8, 8, 0x1.1dfb588b3b584p-3, 0x1.1dfee085a6777p-3},
    {2, 8, 0x1.1e13518b6e49cp-5, 0x1.1dfefc587097dp-3},
  };
  for (const auto &point : points) {
    Geographic input(point[0] * M_PI / 180.0, point[1] * M_PI / 180.0);
    Geographic output;
    assert(geo_transform.forward(input, output));
    assert(bg::get<0>(output) == point[2] && bg::get<1>(output) == point[3]);
  }
  Geographic input(71.999 * M_PI / 180.0, -42.5 * M_PI / 180.0);
  Cartesian output;
  assert(cart_transform.forward(input, output));
  assert(bg::get<0>(output) == 0x1.37fe9f9e41d7bp+22);
  assert(bg::get<1>(output) == -0x1.916325eb2bc07p+22);
}

int main()
{
  check_primitives();
  check_buffer();
  check_transform();
  std::puts("PASS: geometry primitives, special values, buffer vertices/area and coordinate transforms");
}
