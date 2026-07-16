// SPDX-License-Identifier: GPL-3.0-or-later
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <limits>
#include <new>
#include <thread>
#include <vector>

#if defined(_WIN32)
#  define QSDF_API extern "C" __declspec(dllexport)
#else
#  define QSDF_API extern "C" __attribute__((visibility("default")))
#endif

namespace {

constexpr int QSDF_OK = 0;
constexpr int QSDF_INVALID_ARGUMENT = 1;
constexpr int QSDF_OUT_OF_MEMORY = 3;
constexpr int QSDF_CANCELLED = 4;

struct Vec3 {
    double x = 0.0;
    double y = 0.0;
    double z = 0.0;
};

Vec3 add_scaled(Vec3 value, const float* source, double scale) {
    value.x += double(source[0]) * scale;
    value.y += double(source[1]) * scale;
    value.z += double(source[2]) * scale;
    return value;
}

double length(Vec3 value) {
    return std::sqrt(value.x * value.x + value.y * value.y + value.z * value.z);
}

bool normalize(Vec3& value) {
    const double magnitude = length(value);
    if (!std::isfinite(magnitude) || magnitude <= 1.0e-12) return false;
    value.x /= magnitude;
    value.y /= magnitude;
    value.z /= magnitude;
    return true;
}

double dot(Vec3 first, Vec3 second) {
    return first.x * second.x + first.y * second.y + first.z * second.z;
}

Vec3 cross(Vec3 first, Vec3 second) {
    return {
        first.y * second.z - first.z * second.y,
        first.z * second.x - first.x * second.z,
        first.x * second.y - first.y * second.x,
    };
}

/*
 * The parabolic lower-envelope distance transform below is adapted from the
 * implementation accompanying "Distance Transforms of Sampled Functions".
 * Copyright (C) 2006 Pedro Felzenszwalb.
 * Original code: GPL-2.0-or-later.
 * Source: https://cs.brown.edu/people/pfelzens/dt/
 * Quick SDF modifications: GPL-3.0-or-later; see THIRD_PARTY_NOTICES.md.
 */
void dt_1d(const double* f, double* d, int n, std::vector<int>& v,
           std::vector<double>& z) {
    int k = 0;
    v[0] = 0;
    z[0] = -std::numeric_limits<double>::infinity();
    z[1] = std::numeric_limits<double>::infinity();
    for (int q = 1; q < n; ++q) {
        double s = 0.0;
        for (;;) {
            const int vk = v[k];
            s = ((f[q] + double(q) * q) - (f[vk] + double(vk) * vk)) /
                (2.0 * (q - vk));
            if (s > z[k] || k == 0) {
                break;
            }
            --k;
        }
        ++k;
        v[k] = q;
        z[k] = s;
        z[k + 1] = std::numeric_limits<double>::infinity();
    }
    k = 0;
    for (int q = 0; q < n; ++q) {
        while (z[k + 1] < q) {
            ++k;
        }
        const double delta = double(q - v[k]);
        d[q] = delta * delta + f[v[k]];
    }
}

bool dt_2d(std::vector<double>& grid, int width, int height,
           const int* cancel_requested = nullptr) {
    const int max_dim = std::max(width, height);
    std::vector<double> f(max_dim), d(max_dim), z(max_dim + 1);
    std::vector<int> v(max_dim);
    for (int y = 0; y < height; ++y) {
        if (cancel_requested && *cancel_requested) return false;
        for (int x = 0; x < width; ++x) f[x] = grid[size_t(y) * width + x];
        dt_1d(f.data(), d.data(), width, v, z);
        for (int x = 0; x < width; ++x) grid[size_t(y) * width + x] = d[x];
    }
    for (int x = 0; x < width; ++x) {
        if (cancel_requested && *cancel_requested) return false;
        for (int y = 0; y < height; ++y) f[y] = grid[size_t(y) * width + x];
        dt_1d(f.data(), d.data(), height, v, z);
        for (int y = 0; y < height; ++y) grid[size_t(y) * width + x] = d[y];
    }
    for (size_t index = 0; index < grid.size(); ++index) {
        if ((index & size_t(65535)) == 0 && cancel_requested && *cancel_requested)
            return false;
        grid[index] = std::sqrt(std::max(0.0, grid[index]));
    }
    return true;
}

bool signed_distance(const uint8_t* light_mask, int width, int height,
                     std::vector<double>& output,
                     const int* cancel_requested = nullptr) {
    const size_t pixels = size_t(width) * height;
    const double far_sq = double(width) * width + double(height) * height + 100.0;
    std::vector<double> to_shadow(pixels), to_light(pixels);
    bool has_light = false;
    bool has_shadow = false;
    for (size_t p = 0; p < pixels; ++p) {
        if ((p & size_t(65535)) == 0 && cancel_requested && *cancel_requested)
            return false;
        const bool light = light_mask[p] != 0;
        has_light |= light;
        has_shadow |= !light;
        to_shadow[p] = light ? far_sq : 0.0;
        to_light[p] = light ? 0.0 : far_sq;
    }
    output.resize(pixels);
    if (!has_shadow) {
        std::fill(output.begin(), output.end(), -std::numeric_limits<float>::infinity());
        return true;
    }
    if (!has_light) {
        std::fill(output.begin(), output.end(), std::numeric_limits<float>::infinity());
        return true;
    }
    if (!dt_2d(to_shadow, width, height, cancel_requested) ||
        !dt_2d(to_light, width, height, cancel_requested))
        return false;
    for (size_t p = 0; p < pixels; ++p) {
        if ((p & size_t(65535)) == 0 && cancel_requested && *cancel_requested)
            return false;
        // Positive is shadow, negative is light, matching QuickSDF's convention.
        output[p] = to_light[p] - to_shadow[p];
    }
    return true;
}

constexpr uint32_t QSDF_DISTANCE_INFINITY =
    std::numeric_limits<uint32_t>::max() / uint32_t(4);

void dt_1d_squared_u32(const uint32_t* f, uint32_t* d, int n,
                       std::vector<int>& v, std::vector<double>& z) {
    int k = 0;
    v[0] = 0;
    z[0] = -std::numeric_limits<double>::infinity();
    z[1] = std::numeric_limits<double>::infinity();
    for (int q = 1; q < n; ++q) {
        double s = 0.0;
        for (;;) {
            const int vk = v[k];
            s = ((double(f[q]) + double(q) * q) -
                 (double(f[vk]) + double(vk) * vk)) /
                (2.0 * (q - vk));
            if (s > z[k] || k == 0) break;
            --k;
        }
        ++k;
        v[k] = q;
        z[k] = s;
        z[k + 1] = std::numeric_limits<double>::infinity();
    }
    k = 0;
    for (int q = 0; q < n; ++q) {
        while (z[k + 1] < q) ++k;
        const int64_t delta = int64_t(q - v[k]);
        const uint64_t value = uint64_t(f[v[k]]) + uint64_t(delta * delta);
        d[q] = uint32_t(std::min<uint64_t>(value, QSDF_DISTANCE_INFINITY));
    }
}

bool binary_squared_distance(const uint8_t* light_mask, bool feature_is_light,
                             int width, int height, std::vector<uint32_t>& output,
                             const int* cancel_requested) {
    const size_t pixels = size_t(width) * size_t(height);
    output.assign(pixels, QSDF_DISTANCE_INFINITY);
    bool has_feature = false;
    for (size_t pixel = 0; pixel < pixels; ++pixel) {
        const bool is_light = light_mask[pixel] != 0;
        has_feature |= is_light == feature_is_light;
    }
    if (!has_feature) return true;

    // Vertical distance to the closest feature is a pair of linear sweeps.
    const size_t vertical_worker_count = std::max<size_t>(
        1, std::min<size_t>({size_t(16), (size_t(width) + 31) / 32,
                            size_t(std::max(1u, std::thread::hardware_concurrency()))}));
    auto transform_columns = [&](int begin, int end) {
        for (int x = begin; x < end; ++x) {
            if (cancel_requested && *cancel_requested) return;
            int closest = -height - 1;
            for (int y = 0; y < height; ++y) {
                const size_t pixel = size_t(y) * width + x;
                if ((light_mask[pixel] != 0) == feature_is_light) closest = y;
                if (closest >= 0) {
                    const uint32_t delta = uint32_t(y - closest);
                    output[pixel] = delta * delta;
                }
            }
            closest = height * 2;
            for (int y = height - 1; y >= 0; --y) {
                const size_t pixel = size_t(y) * width + x;
                if ((light_mask[pixel] != 0) == feature_is_light) closest = y;
                if (closest < height) {
                    const uint32_t delta = uint32_t(closest - y);
                    output[pixel] = std::min(output[pixel], delta * delta);
                }
            }
        }
    };
    std::vector<std::thread> vertical_threads;
    try {
        vertical_threads.reserve(vertical_worker_count - 1);
        for (size_t worker = 1; worker < vertical_worker_count; ++worker) {
            const int begin = int(size_t(width) * worker / vertical_worker_count);
            const int end = int(size_t(width) * (worker + 1) / vertical_worker_count);
            vertical_threads.emplace_back(transform_columns, begin, end);
        }
        transform_columns(0, int(size_t(width) / vertical_worker_count));
        for (auto& thread : vertical_threads) thread.join();
    } catch (...) {
        for (auto& thread : vertical_threads) if (thread.joinable()) thread.join();
        throw;
    }
    if (cancel_requested && *cancel_requested) return false;

    // The horizontal lower-envelope pass is independent per row.  Use a
    // bounded number of workers and one scratch workspace per worker.
    const size_t minimum_rows_per_worker = 32;
    const size_t useful_workers =
        (size_t(height) + minimum_rows_per_worker - 1) / minimum_rows_per_worker;
    const size_t worker_count = std::max<size_t>(
        1, std::min<size_t>({size_t(16), useful_workers,
                            size_t(std::max(1u, std::thread::hardware_concurrency()))}));
    auto transform_rows = [&](int begin, int end) {
        std::vector<uint32_t> source(static_cast<size_t>(width));
        std::vector<uint32_t> distance(static_cast<size_t>(width));
        std::vector<int> envelope(static_cast<size_t>(width));
        std::vector<double> intersections(static_cast<size_t>(width) + 1);
        for (int y = begin; y < end; ++y) {
            if (cancel_requested && *cancel_requested) return;
            const size_t offset = size_t(y) * width;
            std::copy_n(output.data() + offset, width, source.data());
            dt_1d_squared_u32(source.data(), distance.data(), width,
                              envelope, intersections);
            std::copy_n(distance.data(), width, output.data() + offset);
        }
    };
    std::vector<std::thread> threads;
    try {
        threads.reserve(worker_count > 0 ? worker_count - 1 : 0);
        for (size_t worker = 1; worker < worker_count; ++worker) {
            const int begin = int(size_t(height) * worker / worker_count);
            const int end = int(size_t(height) * (worker + 1) / worker_count);
            threads.emplace_back(transform_rows, begin, end);
        }
        transform_rows(0, int(size_t(height) / worker_count));
        for (auto& thread : threads) thread.join();
    } catch (...) {
        for (auto& thread : threads) if (thread.joinable()) thread.join();
        throw;
    }
    return !(cancel_requested && *cancel_requested);
}

struct BinaryDistanceField {
    std::vector<uint32_t> to_shadow;
    std::vector<uint32_t> to_light;
    bool has_light = false;
    bool has_shadow = false;
};

bool binary_distance_field(const uint8_t* light_mask, int width, int height,
                           BinaryDistanceField& output,
                           const int* cancel_requested) {
    const size_t pixels = size_t(width) * size_t(height);
    output.has_light = false;
    output.has_shadow = false;
    for (size_t pixel = 0; pixel < pixels; ++pixel) {
        if ((pixel & size_t(65535)) == 0 && cancel_requested && *cancel_requested)
            return false;
        const bool light = light_mask[pixel] != 0;
        output.has_light |= light;
        output.has_shadow |= !light;
    }
    if (output.has_shadow &&
        !binary_squared_distance(light_mask, false, width, height,
                                 output.to_shadow, cancel_requested))
        return false;
    if (output.has_light &&
        !binary_squared_distance(light_mask, true, width, height,
                                 output.to_light, cancel_requested))
        return false;
    if (!output.has_shadow) output.to_shadow.clear();
    if (!output.has_light) output.to_light.clear();
    return true;
}

double binary_signed_magnitude(const BinaryDistanceField& field, size_t pixel) {
    if (!field.has_light || !field.has_shadow)
        return std::numeric_limits<double>::infinity();
    const double to_light = std::sqrt(double(field.to_light[pixel]));
    const double to_shadow = std::sqrt(double(field.to_shadow[pixel]));
    return std::abs(to_light - to_shadow);
}

double finite_signed_distance(double value, double diagonal) {
    if (std::isnan(value)) return 0.0;
    if (std::isinf(value)) return std::signbit(value) ? -diagonal : diagonal;
    return value;
}

uint16_t encode_transition(double normalized_angle) {
    normalized_angle = std::clamp(normalized_angle, 0.0, 1.0);
    return uint16_t(std::floor((1.0 - normalized_angle) * 65535.0 + 0.5));
}

bool generate_channel_from_transitions(
    const uint8_t* transitions, const double* angles, int count,
    int width, int height, uint16_t* output, int pixel_stride,
    const int* cancel_requested, int* progress_completed) {
    const size_t pixels = size_t(width) * size_t(height);
    std::vector<uint8_t> previous(pixels), current(pixels);
    for (size_t pixel = 0; pixel < pixels; ++pixel) {
        if ((pixel & size_t(65535)) == 0 && cancel_requested && *cancel_requested)
            return false;
        previous[pixel] = transitions[pixel] == 0 ? 1 : 0;
        output[pixel * size_t(pixel_stride)] = previous[pixel] ? 65535 : 0;
    }
    BinaryDistanceField previous_distance, current_distance;
    if (!binary_distance_field(previous.data(), width, height, previous_distance,
                               cancel_requested))
        return false;
    if (progress_completed) *progress_completed = 1;
    for (int index = 1; index < count; ++index) {
        if (cancel_requested && *cancel_requested) return false;
        bool has_transition = false;
        bool masks_equal = true;
        for (size_t pixel = 0; pixel < pixels; ++pixel) {
            current[pixel] = transitions[pixel] <= index ? 1 : 0;
            const bool changed = previous[pixel] == 0 && current[pixel] != 0;
            has_transition |= changed;
            masks_equal &= current[pixel] == previous[pixel];
        }
        if (!masks_equal && !binary_distance_field(current.data(), width, height,
                                          current_distance, cancel_requested)) {
            return false;
        }
        if (has_transition) {
            const double angle0 = std::abs(angles[index - 1]) / 90.0;
            const double angle1 = std::abs(angles[index]) / 90.0;
            for (size_t pixel = 0; pixel < pixels; ++pixel) {
                if ((pixel & size_t(65535)) == 0 &&
                    cancel_requested && *cancel_requested)
                    return false;
                if (previous[pixel] == 0 && current[pixel] != 0) {
                    const double d0 = binary_signed_magnitude(previous_distance, pixel);
                    const double d1 = binary_signed_magnitude(current_distance, pixel);
                    const double denominator = d0 + d1;
                    const double ratio =
                        std::isfinite(denominator) && denominator > 0.0
                            ? d0 / denominator
                            : 0.5;
                    output[pixel * size_t(pixel_stride)] =
                        encode_transition(angle0 + (angle1 - angle0) * ratio);
                }
            }
        }
        previous.swap(current);
        if (!masks_equal) previous_distance = std::move(current_distance);
        if (progress_completed) *progress_completed = index + 1;
    }
    return true;
}

bool build_side_sequence(const double* angles, int count,
                         std::vector<int>& sequence) {
    if (!angles || count < 2) return false;
    sequence.resize(size_t(count));
    for (int i = 0; i < count; ++i) {
        if (!std::isfinite(angles[i]) || angles[i] < -1.0e-7 ||
            angles[i] > 90.0 + 1.0e-7)
            return false;
        sequence[size_t(i)] = i;
    }
    std::stable_sort(sequence.begin(), sequence.end(),
                     [angles](int first, int second) {
                         return angles[first] < angles[second];
                     });
    if (std::abs(angles[sequence.front()]) > 1.0e-7 ||
        std::abs(angles[sequence.back()] - 90.0) > 1.0e-7)
        return false;
    for (int i = 1; i < count; ++i)
        if (angles[sequence[size_t(i)]] - angles[sequence[size_t(i - 1)]] <=
            1.0e-7)
            return false;
    return true;
}

bool build_guide_directions(const double* angles, int count, const double* forward,
                            const double* up, int side_sign,
                            std::vector<Vec3>& directions) {
    if (!angles || !forward || !up || count < 2 ||
        (side_sign != 1 && side_sign != -1))
        return false;
    Vec3 up_vector{up[0], up[1], up[2]};
    Vec3 front{forward[0], forward[1], forward[2]};
    if (!normalize(up_vector) || !normalize(front)) return false;
    const double vertical = dot(front, up_vector);
    front.x -= up_vector.x * vertical;
    front.y -= up_vector.y * vertical;
    front.z -= up_vector.z * vertical;
    if (!normalize(front)) return false;
    Vec3 side = cross(up_vector, front);
    if (!normalize(side)) return false;
    side.x *= side_sign;
    side.y *= side_sign;
    side.z *= side_sign;
    if (std::abs(angles[0]) > 1.0e-7 ||
        std::abs(angles[count - 1] - 90.0) > 1.0e-7)
        return false;
    directions.resize(size_t(count));
    for (int index = 0; index < count; ++index) {
        const double angle = angles[index];
        if (!std::isfinite(angle) || angle < -1.0e-7 || angle > 90.0 + 1.0e-7 ||
            (index > 0 && angle - angles[index - 1] <= 1.0e-7))
            return false;
        const double progress = std::clamp(double(angle) / 90.0, 0.0, 1.0);
        const double front_dot = progress - 0.5;
        const double side_dot = std::sqrt(std::max(0.0, 1.0 - front_dot * front_dot));
        Vec3 direction{
            side.x * side_dot + front.x * front_dot,
            side.y * side_dot + front.y * front_dot,
            side.z * side_dot + front.z * front_dot,
        };
        if (!normalize(direction)) return false;
        directions[size_t(index)] = direction;
    }
    return true;
}

int rasterize_guide_normals(const float* triangle_uvs, const float* corner_normals,
                            int triangle_count, int width, int height,
                            std::vector<Vec3>& normal_image,
                            uint8_t* output_occupancy,
                            const int* cancel_requested) {
    const size_t pixels = size_t(width) * size_t(height);
    normal_image.assign(pixels, Vec3{});
    std::fill(output_occupancy, output_occupancy + pixels, uint8_t(0));
    for (int triangle = 0; triangle < triangle_count; ++triangle) {
        if (cancel_requested && *cancel_requested) return QSDF_CANCELLED;
        const float* uv = triangle_uvs + size_t(triangle) * 6;
        const float* normal = corner_normals + size_t(triangle) * 9;
        double x[3], y[3];
        for (int corner = 0; corner < 3; ++corner) {
            x[corner] = double(uv[corner * 2]) * width - 0.5;
            y[corner] = (1.0 - double(uv[corner * 2 + 1])) * height - 0.5;
        }
        const int x0 = std::max(0, int(std::ceil(std::min({x[0], x[1], x[2]}))));
        const int x1 = std::min(width - 1, int(std::floor(std::max({x[0], x[1], x[2]}))));
        const int y0 = std::max(0, int(std::ceil(std::min({y[0], y[1], y[2]}))));
        const int y1 = std::min(height - 1, int(std::floor(std::max({y[0], y[1], y[2]}))));
        if (x1 < x0 || y1 < y0) continue;
        const double denominator =
            (y[1] - y[2]) * (x[0] - x[2]) +
            (x[2] - x[1]) * (y[0] - y[2]);
        if (std::abs(denominator) <= 1.0e-12) continue;
        for (int row = y0; row <= y1; ++row) {
            for (int column = x0; column <= x1; ++column) {
                const double w0 =
                    ((y[1] - y[2]) * (column - x[2]) +
                     (x[2] - x[1]) * (row - y[2])) /
                    denominator;
                const double w1 =
                    ((y[2] - y[0]) * (column - x[2]) +
                     (x[0] - x[2]) * (row - y[2])) /
                    denominator;
                const double w2 = 1.0 - w0 - w1;
                if (w0 < -1.0e-9 || w1 < -1.0e-9 || w2 < -1.0e-9) continue;
                Vec3 value{};
                value = add_scaled(value, normal, w0);
                value = add_scaled(value, normal + 3, w1);
                value = add_scaled(value, normal + 6, w2);
                if (!normalize(value)) {
                    int dominant = 0;
                    if (w1 > w0) dominant = 1;
                    if (w2 > (dominant == 0 ? w0 : w1)) dominant = 2;
                    value = {normal[dominant * 3], normal[dominant * 3 + 1],
                             normal[dominant * 3 + 2]};
                    if (!normalize(value)) return QSDF_INVALID_ARGUMENT;
                }
                const size_t pixel = size_t(row) * width + column;
                normal_image[pixel] = value;
                output_occupancy[pixel] = 1;
            }
        }
    }
    return QSDF_OK;
}

}  // namespace

QSDF_API int qsdf_version() { return 8; }

QSDF_API int qsdf_interpolate_binary_masks(
    const uint8_t* first_mask, const uint8_t* second_mask,
    int width, int height, double factor, uint8_t* output_mask,
    const int* cancel_requested) {
    if (!first_mask || !second_mask || !output_mask || width < 1 || height < 1 ||
        !std::isfinite(factor) || factor < 0.0 || factor > 1.0)
        return QSDF_INVALID_ARGUMENT;
    if (cancel_requested && *cancel_requested) return QSDF_CANCELLED;
    const size_t pixels = size_t(width) * size_t(height);
    if (factor == 0.0 || factor == 1.0) {
        const uint8_t* source = factor == 0.0 ? first_mask : second_mask;
        for (size_t pixel = 0; pixel < pixels; ++pixel) {
            if ((pixel & size_t(65535)) == 0 && cancel_requested && *cancel_requested)
                return QSDF_CANCELLED;
            output_mask[pixel] = source[pixel] != 0 ? 1 : 0;
        }
        return QSDF_OK;
    }
    try {
        std::vector<double> first_sdf, second_sdf;
        if (!signed_distance(first_mask, width, height, first_sdf,
                             cancel_requested) ||
            !signed_distance(second_mask, width, height, second_sdf,
                             cancel_requested))
            return QSDF_CANCELLED;
        const double diagonal =
            std::sqrt(double(width) * width + double(height) * height) + 1.0;
        const double inverse_factor = 1.0 - factor;
        for (size_t pixel = 0; pixel < pixels; ++pixel) {
            if ((pixel & size_t(65535)) == 0 && cancel_requested && *cancel_requested)
                return QSDF_CANCELLED;
            const double first = finite_signed_distance(first_sdf[pixel], diagonal);
            const double second = finite_signed_distance(second_sdf[pixel], diagonal);
            output_mask[pixel] =
                inverse_factor * first + factor * second <= 0.0 ? 1 : 0;
        }
        return QSDF_OK;
    } catch (const std::bad_alloc&) {
        return QSDF_OUT_OF_MEMORY;
    }
}

QSDF_API int qsdf_bake_face_shadow_guide(
    const float* triangle_uvs, const float* corner_normals, int triangle_count,
    const double* angles, int angle_count, const double* forward, const double* up,
    int side_sign, double cutoff, int width, int height, uint8_t* output_masks,
    uint8_t* output_occupancy, const int* cancel_requested) {
    if (!triangle_uvs || !corner_normals || !angles || !forward || !up ||
        !output_masks || !output_occupancy || triangle_count < 0 ||
        angle_count < 2 || width < 1 || height < 1 || !std::isfinite(cutoff))
        return QSDF_INVALID_ARGUMENT;
    try {
        const size_t pixels = size_t(width) * size_t(height);
        std::vector<Vec3> normal_image;
        const int rasterized = rasterize_guide_normals(
            triangle_uvs, corner_normals, triangle_count, width, height,
            normal_image, output_occupancy, cancel_requested);
        if (rasterized != QSDF_OK) return rasterized;
        std::vector<Vec3> directions;
        if (!build_guide_directions(angles, angle_count, forward, up, side_sign,
                                    directions))
            return QSDF_INVALID_ARGUMENT;
        for (int angle = 0; angle < angle_count; ++angle) {
            if (cancel_requested && *cancel_requested) return QSDF_CANCELLED;
            uint8_t* mask = output_masks + size_t(angle) * pixels;
            const Vec3 direction = directions[size_t(angle)];
            for (size_t pixel = 0; pixel < pixels; ++pixel)
                mask[pixel] = uint8_t(!output_occupancy[pixel] ||
                                      dot(normal_image[pixel], direction) >= cutoff);
        }
        for (int angle = 1; angle < angle_count; ++angle) {
            const uint8_t* previous = output_masks + size_t(angle - 1) * pixels;
            uint8_t* current = output_masks + size_t(angle) * pixels;
            for (size_t pixel = 0; pixel < pixels; ++pixel)
                current[pixel] = uint8_t(current[pixel] || previous[pixel]);
        }
        // The final authoring key represents Full Light. Paint overrides are
        // composited later, so the Base Guide itself must illuminate every
        // occupied texel at this endpoint.
        uint8_t* final_mask = output_masks + size_t(angle_count - 1) * pixels;
        for (size_t pixel = 0; pixel < pixels; ++pixel)
            if (output_occupancy[pixel]) final_mask[pixel] = 1;
        return QSDF_OK;
    } catch (const std::bad_alloc&) {
        return QSDF_OUT_OF_MEMORY;
    }
}

QSDF_API int qsdf_repair_lane_bits(
    const uint16_t* display_bits, const uint16_t* base_bits,
    const uint16_t* coverage_bits, int count, int width, int height,
    uint8_t* output_transition, uint8_t* output_changed_count,
    int* changed_samples, int* changed_pixels,
    int* protected_changed_samples, int* protected_changed_pixels,
    const int* cancel_requested) {
    if (!display_bits || !base_bits || !coverage_bits || !output_transition ||
        !output_changed_count || !changed_samples || !changed_pixels ||
        !protected_changed_samples || !protected_changed_pixels ||
        count < 1 || count > 16 || width < 1 || height < 1)
        return QSDF_INVALID_ARGUMENT;
    if (cancel_requested && *cancel_requested) return QSDF_CANCELLED;
    const size_t pixels = size_t(width) * size_t(height);
    struct RepairCounts {
        int changed_samples = 0;
        int changed_pixels = 0;
        int protected_changed_samples = 0;
        int protected_changed_pixels = 0;
    };
    auto repair_range = [&](size_t begin, size_t end, RepairCounts& counts) {
        for (size_t pixel = begin; pixel < end; ++pixel) {
            if ((pixel & size_t(4095)) == 0 && cancel_requested && *cancel_requested)
                return;
            const uint16_t display_word = display_bits[pixel];
            const uint16_t base_word = base_bits[pixel];
            const uint16_t coverage_word = coverage_bits[pixel];
            int protected_cost = 0;
            int display_cost = 0;
            int base_cost = 0;
            for (int sample = 0; sample < count; ++sample) {
                const uint16_t bit = uint16_t(1u << sample);
                const bool display = (display_word & bit) != 0;
                const bool base = (base_word & bit) != 0;
                const bool protected_value =
                    (coverage_word & bit) != 0 || display != base;
                if (!display) {
                    ++display_cost;
                    if (protected_value) ++protected_cost;
                }
                if (!base) ++base_cost;
            }
            int best_protected = protected_cost;
            int best_display = display_cost;
            int best_base = base_cost;
            int best_transition = 0;
            for (int transition = 1; transition <= count; ++transition) {
                const uint16_t bit = uint16_t(1u << (transition - 1));
                const bool display = (display_word & bit) != 0;
                const bool base = (base_word & bit) != 0;
                const bool protected_value =
                    (coverage_word & bit) != 0 || display != base;
                const int display_delta = display ? 1 : -1;
                if (protected_value) protected_cost += display_delta;
                display_cost += display_delta;
                base_cost += base ? 1 : -1;
                if (protected_cost < best_protected ||
                    (protected_cost == best_protected &&
                     (display_cost < best_display ||
                      (display_cost == best_display && base_cost < best_base)))) {
                    best_protected = protected_cost;
                    best_display = display_cost;
                    best_base = base_cost;
                    best_transition = transition;
                }
            }
            output_transition[pixel] = uint8_t(best_transition);
            uint8_t pixel_changed_count = 0;
            uint8_t protected_pixel_changed_count = 0;
            for (int sample = 0; sample < count; ++sample) {
                const uint16_t bit = uint16_t(1u << sample);
                const bool display = (display_word & bit) != 0;
                const bool base = (base_word & bit) != 0;
                const bool repaired = sample >= best_transition;
                const bool changed = repaired != display;
                const bool protected_value =
                    (coverage_word & bit) != 0 || display != base;
                if (changed) {
                    ++pixel_changed_count;
                    if (protected_value) ++protected_pixel_changed_count;
                }
            }
            output_changed_count[pixel] = pixel_changed_count;
            counts.changed_samples += pixel_changed_count;
            counts.protected_changed_samples += protected_pixel_changed_count;
            if (pixel_changed_count) ++counts.changed_pixels;
            if (protected_pixel_changed_count) ++counts.protected_changed_pixels;
        }
    };
    const size_t minimum_pixels_per_worker = 65536;
    const size_t useful_workers =
        (pixels + minimum_pixels_per_worker - 1) / minimum_pixels_per_worker;
    const size_t worker_count = std::max<size_t>(
        1, std::min<size_t>({size_t(16), useful_workers,
                            size_t(std::max(1u, std::thread::hardware_concurrency()))}));
    std::vector<RepairCounts> counts(worker_count);
    std::vector<std::thread> threads;
    try {
        threads.reserve(worker_count > 0 ? worker_count - 1 : 0);
        for (size_t worker = 1; worker < worker_count; ++worker) {
            const size_t begin = pixels * worker / worker_count;
            const size_t end = pixels * (worker + 1) / worker_count;
            threads.emplace_back(repair_range, begin, end, std::ref(counts[worker]));
        }
        repair_range(0, pixels / worker_count, counts[0]);
        for (auto& thread : threads) thread.join();
    } catch (...) {
        for (auto& thread : threads) if (thread.joinable()) thread.join();
        return QSDF_OUT_OF_MEMORY;
    }
    if (cancel_requested && *cancel_requested) return QSDF_CANCELLED;
    *changed_samples = *changed_pixels = 0;
    *protected_changed_samples = *protected_changed_pixels = 0;
    for (const auto& value : counts) {
        *changed_samples += value.changed_samples;
        *changed_pixels += value.changed_pixels;
        *protected_changed_samples += value.protected_changed_samples;
        *protected_changed_pixels += value.protected_changed_pixels;
    }
    return QSDF_OK;
}

QSDF_API int qsdf_generate_threshold_transitions(
    const uint8_t* transitions, const double* angles, int count,
    int width, int height, uint16_t* output, int pixel_stride,
    int channel_offset, const int* cancel_requested, int* progress_completed) {
    if (!transitions || !angles || !output || count < 2 || count > 16 ||
        width < 1 || height < 1 || pixel_stride < 1 || channel_offset < 0 ||
        channel_offset >= pixel_stride)
        return QSDF_INVALID_ARGUMENT;
    if (cancel_requested && *cancel_requested) return QSDF_CANCELLED;
    if (progress_completed) *progress_completed = 0;
    std::vector<int> sequence;
    if (!build_side_sequence(angles, count, sequence)) return QSDF_INVALID_ARGUMENT;
    for (int index = 0; index < count; ++index)
        if (sequence[size_t(index)] != index) return QSDF_INVALID_ARGUMENT;
    const size_t pixels = size_t(width) * size_t(height);
    for (size_t pixel = 0; pixel < pixels; ++pixel)
        if (transitions[pixel] > count) return QSDF_INVALID_ARGUMENT;
    try {
        if (!generate_channel_from_transitions(
                transitions, angles, count, width, height,
                output + channel_offset, pixel_stride, cancel_requested,
                progress_completed))
            return QSDF_CANCELLED;
        return QSDF_OK;
    } catch (const std::bad_alloc&) {
        return QSDF_OUT_OF_MEMORY;
    }
}
