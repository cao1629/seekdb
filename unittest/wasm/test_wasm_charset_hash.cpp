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

#include "lib/charset/ob_ctype.h"
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>

extern "C" void right_to_die_or_duty_to_live_c()
{
  std::abort();
}

struct CharsetHashCase
{
  size_t collation;
  const char *text;
  uint64_t seed;
  bool calc_end_space;
  uint64_t expected[2];
  uint64_t expected_legacy_seed;
};

int main()
{
  ObCharsetInfo simple = ob_charset_utf8mb4_general_ci;
  simple.name = "8bit_simple";
  simple.coll = &ob_collation_8bit_simple_ci_handler;
  ObCharsetInfo eight_bit = ob_charset_utf8mb4_general_ci;
  eight_bit.name = "8bit_binary";
  eight_bit.coll = &ob_collation_8bit_bin_handler;
  const ObCharsetInfo *collations[] = {
    &ob_charset_bin, &simple, &eight_bit,
    &ob_charset_utf8mb4_bin, &ob_charset_utf8mb4_general_ci
  };
  const CharsetHashCase cases[] = {
    {0, "tokenize", 0x0000000000000000ULL, false,
      {0xa1448c250c34958eULL, 0xd28420f78755e48aULL}, 0xc6a4a7935bd1e9adULL},
    {0, "tokenize", 0x123456789abcdef0ULL, false,
      {0x262ee8f796872e8eULL, 0x66da227975ed63b5ULL}, 0xc6a4a7935bd1e9adULL},
    {0, "caf\xc3\xa9 \xe4\xb8\xad\xf0\x9f\x98\x80", 0x123456789abcdef0ULL, false,
      {0x24c6f45c70807569ULL, 0x7b0775d2ae7a5483ULL}, 0xc6a4a7935bd1e9bcULL},
    {0, "OceanBase  ", 0x123456789abcdef0ULL, false,
      {0x210290f0f88115f1ULL, 0xd27b28178cdc7ae9ULL}, 0xc6a4a7935bd1e9b6ULL},
    {0, "OceanBase  ", 0x123456789abcdef0ULL, true,
      {0x210290f0f88115f1ULL, 0xd27b28178cdc7ae9ULL}, 0xc6a4a7935bd1e9b6ULL},
    {0, "", 0x123456789abcdef0ULL, false,
      {0x123456789abcdef0ULL, 0xe86dd1b61069dc9fULL}, 0xc6a4a7935bd1e995ULL},
    {1, "tokenize", 0x0000000000000000ULL, false,
      {0xdcab8328a4c06a0eULL, 0x49ab9623788c156bULL}, 0xc6a4a7935bd1e9adULL},
    {1, "tokenize", 0x123456789abcdef0ULL, false,
      {0x4d33fc6b0e68280eULL, 0x86de161cfe5de071ULL}, 0xc6a4a7935bd1e9adULL},
    {1, "caf\xc3\xa9 \xe4\xb8\xad\xf0\x9f\x98\x80", 0x123456789abcdef0ULL, false,
      {0x033b71e10df3c2a9ULL, 0x0d6e52a91ccde9deULL}, 0xc6a4a7935bd1e9bcULL},
    {1, "OceanBase  ", 0x123456789abcdef0ULL, false,
      {0xb3fcc689382e5771ULL, 0x8cfec7eba58c9817ULL}, 0xc6a4a7935bd1e9b0ULL},
    {1, "OceanBase  ", 0x123456789abcdef0ULL, true,
      {0x1ef5a1d6a58d73d1ULL, 0xcdbd2a2074d1c70cULL}, 0xc6a4a7935bd1e9b6ULL},
    {1, "", 0x123456789abcdef0ULL, false,
      {0x123456789abcdef0ULL, 0x123456789abcdef0ULL}, 0xc6a4a7935bd1e995ULL},
    {2, "tokenize", 0x0000000000000000ULL, false,
      {0xa1448c250c34958eULL, 0xd28420f78755e48aULL}, 0xc6a4a7935bd1e9adULL},
    {2, "tokenize", 0x123456789abcdef0ULL, false,
      {0x262ee8f796872e8eULL, 0x66da227975ed63b5ULL}, 0xc6a4a7935bd1e9adULL},
    {2, "caf\xc3\xa9 \xe4\xb8\xad\xf0\x9f\x98\x80", 0x123456789abcdef0ULL, false,
      {0x24c6f45c70807569ULL, 0x7b0775d2ae7a5483ULL}, 0xc6a4a7935bd1e9bcULL},
    {2, "OceanBase  ", 0x123456789abcdef0ULL, false,
      {0x13987d9c821a3151ULL, 0x76b70f90eb851278ULL}, 0xc6a4a7935bd1e9b0ULL},
    {2, "OceanBase  ", 0x123456789abcdef0ULL, true,
      {0x210290f0f88115f1ULL, 0xd27b28178cdc7ae9ULL}, 0xc6a4a7935bd1e9b6ULL},
    {2, "", 0x123456789abcdef0ULL, false,
      {0x123456789abcdef0ULL, 0xe86dd1b61069dc9fULL}, 0xc6a4a7935bd1e995ULL},
    {3, "tokenize", 0x0000000000000000ULL, false,
      {0xa1448c250c34958eULL, 0xd28420f78755e48aULL}, 0xc6a4a7935bd1e9adULL},
    {3, "tokenize", 0x123456789abcdef0ULL, false,
      {0x262ee8f796872e8eULL, 0x66da227975ed63b5ULL}, 0xc6a4a7935bd1e9adULL},
    {3, "caf\xc3\xa9 \xe4\xb8\xad\xf0\x9f\x98\x80", 0x123456789abcdef0ULL, false,
      {0x24c6f45c70807569ULL, 0x7b0775d2ae7a5483ULL}, 0xc6a4a7935bd1e9bcULL},
    {3, "OceanBase  ", 0x123456789abcdef0ULL, false,
      {0x13987d9c821a3151ULL, 0x76b70f90eb851278ULL}, 0xc6a4a7935bd1e9b0ULL},
    {3, "OceanBase  ", 0x123456789abcdef0ULL, true,
      {0x210290f0f88115f1ULL, 0xd27b28178cdc7ae9ULL}, 0xc6a4a7935bd1e9b6ULL},
    {3, "", 0x123456789abcdef0ULL, false,
      {0x123456789abcdef0ULL, 0xe86dd1b61069dc9fULL}, 0xc6a4a7935bd1e995ULL},
    {4, "tokenize", 0x0000000000000000ULL, false,
      {0xdcdec88485b35341ULL, 0xa4e12a6e889dc04aULL}, 0xc6a4a7935bd1e9c5ULL},
    {4, "tokenize", 0x123456789abcdef0ULL, false,
      {0x74268dfa738e8901ULL, 0x5a20e06eaed59dfbULL}, 0xc6a4a7935bd1e9c5ULL},
    {4, "caf\xc3\xa9 \xe4\xb8\xad\xf0\x9f\x98\x80", 0x123456789abcdef0ULL, false,
      {0x401e27f994a4ec7aULL, 0xb60526c287198e52ULL}, 0xc6a4a7935bd1e9bfULL},
    {4, "OceanBase  ", 0x123456789abcdef0ULL, false,
      {0x90ebf44b34dc0e17ULL, 0x3092247290d581f5ULL}, 0xc6a4a7935bd1e9cbULL},
    {4, "OceanBase  ", 0x123456789abcdef0ULL, true,
      {0x5142b6593c13c957ULL, 0x1e74deff58f3a857ULL}, 0xc6a4a7935bd1e9d7ULL},
    {4, "", 0x123456789abcdef0ULL, false,
      {0x123456789abcdef0ULL, 0x123456789abcdef0ULL}, 0xc6a4a7935bd1e995ULL},
  };
  for (const auto &test : cases) {
    const auto *charset = collations[test.collation];
    for (int use_wyhash = 0; use_wyhash < 2; ++use_wyhash) {
      uint64_t value = test.seed;
      uint64_t second_seed = 0xc6a4a7935bd1e995ULL;
      charset->coll->hash_sort(charset, reinterpret_cast<const unsigned char *>(test.text),
          std::strlen(test.text), &value, &second_seed, test.calc_end_space,
          use_wyhash ? wyhash : nullptr);
      if (value != test.expected[use_wyhash]) {
        std::fprintf(stderr, "%s hash mismatch: seed=%016llx, wyhash=%d, actual=%016llx, expected=%016llx\n",
            charset->name, static_cast<unsigned long long>(test.seed), use_wyhash,
            static_cast<unsigned long long>(value),
            static_cast<unsigned long long>(test.expected[use_wyhash]));
        std::abort();
      }
      assert(second_seed == (use_wyhash ? 0xc6a4a7935bd1e995ULL : test.expected_legacy_seed));
    }
  }
  std::puts("PASS: charset hashes match native 64-bit values for five handlers");
}
