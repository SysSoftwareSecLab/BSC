#include <moveit/collision_detection/collision_common.h>
#include <moveit/collision_detection/collision_matrix.h>
#include <moveit/collision_detection/world.h>
#include <moveit/collision_detection_fcl/collision_env_fcl.h>
#include <moveit/robot_model/robot_model.h>
#include <moveit/robot_state/robot_state.h>

#include <geometric_shapes/shapes.h>
#include <srdfdom/model.h>
#include <urdf_parser/urdf_parser.h>

#include <Eigen/Geometry>

#include <algorithm>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <map>
#include <memory>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <tuple>
#include <utility>
#include <vector>

namespace
{
using Row = std::map<std::string, std::string>;

std::string read_file(const std::filesystem::path& path)
{
  std::ifstream stream(path, std::ios::binary);
  if (!stream)
    throw std::runtime_error("cannot open input: " + path.string());
  return { std::istreambuf_iterator<char>(stream), std::istreambuf_iterator<char>() };
}

std::vector<std::string> split(const std::string& line, char delimiter)
{
  std::vector<std::string> result;
  std::string item;
  std::stringstream stream(line);
  while (std::getline(stream, item, delimiter))
  {
    if (!item.empty() && item.back() == '\r')
      item.pop_back();
    result.push_back(item);
  }
  if (!line.empty() && line.back() == delimiter)
    result.emplace_back();
  return result;
}

std::vector<Row> read_tsv(const std::filesystem::path& path, const std::vector<std::string>& required_header)
{
  std::ifstream stream(path);
  if (!stream)
    throw std::runtime_error("cannot open TSV: " + path.string());
  std::string line;
  if (!std::getline(stream, line))
    throw std::runtime_error("TSV has no header: " + path.string());
  const auto header = split(line, '\t');
  if (header != required_header)
    throw std::runtime_error("TSV header mismatch: " + path.string());
  std::vector<Row> rows;
  std::size_t line_number = 1;
  while (std::getline(stream, line))
  {
    ++line_number;
    if (line.empty())
      continue;
    const auto fields = split(line, '\t');
    if (fields.size() != header.size())
      throw std::runtime_error("TSV width mismatch at line " + std::to_string(line_number));
    Row row;
    for (std::size_t index = 0; index < header.size(); ++index)
      row.emplace(header[index], fields[index]);
    rows.push_back(std::move(row));
  }
  return rows;
}

std::vector<Row> read_state_tsv(const std::filesystem::path& path, const std::vector<std::string>& required_variables)
{
  std::ifstream stream(path);
  if (!stream)
    throw std::runtime_error("cannot open state TSV: " + path.string());
  std::string line;
  if (!std::getline(stream, line))
    throw std::runtime_error("state TSV has no header: " + path.string());
  const auto header = split(line, '\t');
  if (header.empty() || header.front() != "scenario")
    throw std::runtime_error("state TSV must begin with scenario");
  const std::set<std::string> actual(header.begin() + 1, header.end());
  const std::set<std::string> required(required_variables.begin(), required_variables.end());
  if (actual.size() != header.size() - 1 || actual != required)
    throw std::runtime_error("state TSV variables do not exactly match the robot model");
  std::vector<Row> rows;
  std::size_t line_number = 1;
  while (std::getline(stream, line))
  {
    ++line_number;
    if (line.empty())
      continue;
    const auto fields = split(line, '\t');
    if (fields.size() != header.size())
      throw std::runtime_error("state TSV width mismatch at line " + std::to_string(line_number));
    Row row;
    for (std::size_t index = 0; index < header.size(); ++index)
      row.emplace(header[index], fields[index]);
    rows.push_back(std::move(row));
  }
  return rows;
}

double number(const std::string& value, const std::string& label)
{
  std::size_t used = 0;
  double result = 0.0;
  try
  {
    result = std::stod(value, &used);
  }
  catch (const std::exception&)
  {
    throw std::runtime_error("invalid number for " + label + ": " + value);
  }
  if (used != value.size() || !std::isfinite(result))
    throw std::runtime_error("invalid finite number for " + label + ": " + value);
  return result;
}

Eigen::Vector3d vector3(const Row& row, const std::string& prefix)
{
  return { number(row.at(prefix + "_x"), prefix + "_x"), number(row.at(prefix + "_y"), prefix + "_y"),
           number(row.at(prefix + "_z"), prefix + "_z") };
}

std::string body_type(collision_detection::BodyType value)
{
  switch (value)
  {
    case collision_detection::BodyTypes::ROBOT_LINK:
      return "robot-link";
    case collision_detection::BodyTypes::ROBOT_ATTACHED:
      return "attached-object";
    case collision_detection::BodyTypes::WORLD_OBJECT:
      return "world";
  }
  throw std::runtime_error("unknown MoveIt body type");
}

collision_detection::WorldPtr make_world(const std::string& scenario, const std::vector<Row>& rows)
{
  auto world = std::make_shared<collision_detection::World>();
  std::set<std::string> ids;
  for (const auto& row : rows)
  {
    if (row.at("scenario") != "*" && row.at("scenario") != scenario)
      continue;
    const auto& id = row.at("object_id");
    if (id.empty() || !ids.insert(id).second)
      throw std::runtime_error("duplicate or empty world object for scenario " + scenario + ": " + id);
    const Eigen::Vector3d center = vector3(row, "center");
    const Eigen::Vector3d size = vector3(row, "size");
    if ((size.array() <= 0.0).any())
      throw std::runtime_error("world box has non-positive size: " + id);
    auto shape = std::make_shared<shapes::Box>(size.x(), size.y(), size.z());
    Eigen::Isometry3d pose = Eigen::Isometry3d::Identity();
    pose.translation() = center;
    world->addToObject(id, shape, pose);
  }
  if (ids.empty())
    throw std::runtime_error("scenario has no world objects: " + scenario);
  return world;
}

void add_attachments(moveit::core::RobotState& state, const std::string& scenario, const std::vector<Row>& rows)
{
  std::set<std::string> ids;
  for (const auto& row : rows)
  {
    if (row.at("scenario") != "*" && row.at("scenario") != scenario)
      continue;
    const auto& id = row.at("body_id");
    if (id.empty() || !ids.insert(id).second)
      throw std::runtime_error("duplicate or empty attached body: " + id);
    const Eigen::Vector3d size = vector3(row, "size");
    if ((size.array() <= 0.0).any())
      throw std::runtime_error("attached box has non-positive size: " + id);
    Eigen::Quaterniond orientation(number(row.at("pose_qw"), "pose_qw"), number(row.at("pose_qx"), "pose_qx"),
                                   number(row.at("pose_qy"), "pose_qy"), number(row.at("pose_qz"), "pose_qz"));
    if (std::abs(orientation.norm() - 1.0) > 1e-12)
      throw std::runtime_error("attachment quaternion must be unit length: " + id);
    Eigen::Isometry3d pose = Eigen::Isometry3d::Identity();
    pose.linear() = orientation.toRotationMatrix();
    pose.translation() = vector3(row, "pose");
    std::vector<shapes::ShapeConstPtr> shapes{ std::make_shared<shapes::Box>(size.x(), size.y(), size.z()) };
    EigenSTL::vector_Isometry3d poses{ pose };
    std::vector<std::string> touch_links;
    if (!row.at("touch_links_csv").empty())
      touch_links = split(row.at("touch_links_csv"), ',');
    state.attachBody(id, Eigen::Isometry3d::Identity(), shapes, poses, touch_links, row.at("parent_link"));
  }
  state.update();
}

struct OutputRow
{
  std::string scenario;
  std::string scope;
  std::string first;
  std::string second;
  double distance;
  Eigen::Vector3d nearest_first;
  Eigen::Vector3d nearest_second;
  std::string first_type;
  std::string second_type;
};

void append_distances(const std::string& scenario, const std::string& scope,
                      const collision_detection::DistanceResult& result, std::vector<OutputRow>& output,
                      std::set<std::tuple<std::string, std::string, std::string>>& unique)
{
  for (const auto& entry : result.distances)
  {
    if (entry.second.empty())
      throw std::runtime_error("distance map contains an empty result vector");
    // MoveIt Humble 2.5.9 exposes DistanceResultsData::operator< without a
    // const qualifier.  The map is intentionally read-only here, so compare
    // the signed-distance field explicitly instead of invoking that operator.
    const auto best = std::min_element(
        entry.second.cbegin(), entry.second.cend(),
        [](const collision_detection::DistanceResultsData& first,
           const collision_detection::DistanceResultsData& second) { return first.distance < second.distance; });
    if (!std::isfinite(best->distance) || best->link_names[0].empty() || best->link_names[1].empty())
      throw std::runtime_error("distance result is incomplete or non-finite");
    std::string first = best->link_names[0];
    std::string second = best->link_names[1];
    Eigen::Vector3d nearest_first = best->nearest_points[0];
    Eigen::Vector3d nearest_second = best->nearest_points[1];
    std::string first_type = body_type(best->body_types[0]);
    std::string second_type = body_type(best->body_types[1]);
    if (second < first)
    {
      std::swap(first, second);
      std::swap(nearest_first, nearest_second);
      std::swap(first_type, second_type);
    }
    const auto map_pair = std::minmax(entry.first.first, entry.first.second);
    if (map_pair.first != first || map_pair.second != second)
      throw std::runtime_error("distance map key and body names disagree");
    if (!unique.emplace(scope, first, second).second)
      throw std::runtime_error("duplicate distance pair: " + scope + ":" + first + ":" + second);
    output.push_back({ scenario, scope, first, second, best->distance, nearest_first, nearest_second, first_type,
                       second_type });
  }
}

void write_output(const std::filesystem::path& output_path, const std::vector<OutputRow>& rows)
{
  if (std::filesystem::exists(output_path))
    throw std::runtime_error("refusing to overwrite output: " + output_path.string());
  const auto temporary = output_path.string() + ".partial";
  if (std::filesystem::exists(temporary))
    throw std::runtime_error("partial output already exists: " + temporary);
  try
  {
    std::ofstream output(temporary, std::ios::binary);
    if (!output)
      throw std::runtime_error("cannot create output: " + temporary);
    output << "scenario\tscope\tentity_first\tentity_second\tsigned_distance_m\tnearest_first_x_m\t"
              "nearest_first_y_m\tnearest_first_z_m\tnearest_second_x_m\tnearest_second_y_m\t"
              "nearest_second_z_m\tfirst_body_type\tsecond_body_type\n";
    output << std::setprecision(std::numeric_limits<double>::max_digits10);
    for (const auto& row : rows)
    {
      output << row.scenario << '\t' << row.scope << '\t' << row.first << '\t' << row.second << '\t'
             << row.distance << '\t' << row.nearest_first.x() << '\t' << row.nearest_first.y() << '\t'
             << row.nearest_first.z() << '\t' << row.nearest_second.x() << '\t' << row.nearest_second.y() << '\t'
             << row.nearest_second.z() << '\t' << row.first_type << '\t' << row.second_type << '\n';
    }
    output.close();
    if (!output)
      throw std::runtime_error("failed while writing output: " + temporary);
    std::filesystem::rename(temporary, output_path);
  }
  catch (...)
  {
    std::error_code ignored;
    std::filesystem::remove(temporary, ignored);
    throw;
  }
}
}  // namespace

int main(int argc, char** argv)
{
  if (argc != 7)
  {
    std::cerr << "usage: openarm_fcl_pair_probe URDF SRDF STATES_TSV WORLD_TSV ATTACHMENTS_TSV OUTPUT_TSV\n";
    return 2;
  }
  try
  {
    const std::filesystem::path urdf_path = argv[1];
    const std::filesystem::path srdf_path = argv[2];
    auto urdf = urdf::parseURDF(read_file(urdf_path));
    if (!urdf)
      throw std::runtime_error("URDF parse failed");
    auto srdf = std::make_shared<srdf::Model>();
    if (!srdf->initString(*urdf, read_file(srdf_path)))
      throw std::runtime_error("SRDF parse failed");
    auto model = std::make_shared<moveit::core::RobotModel>(urdf, srdf);
    collision_detection::AllowedCollisionMatrix acm(*srdf);

    const auto& variable_names = model->getVariableNames();
    const auto states = read_state_tsv(argv[3], variable_names);
    const auto worlds = read_tsv(argv[4], { "scenario", "object_id", "center_x", "center_y", "center_z", "size_x",
                                                   "size_y", "size_z" });
    const auto attachments = read_tsv(
        argv[5], { "scenario", "body_id", "parent_link", "size_x", "size_y", "size_z", "pose_x", "pose_y",
                   "pose_z", "pose_qx", "pose_qy", "pose_qz", "pose_qw", "touch_links_csv" });
    if (states.empty())
      throw std::runtime_error("states table contains no scenarios");

    std::vector<OutputRow> output;
    std::set<std::string> scenarios;
    for (const auto& row : states)
    {
      const auto& scenario = row.at("scenario");
      if (scenario.empty() || !scenarios.insert(scenario).second)
        throw std::runtime_error("state scenario must be non-empty and unique: " + scenario);
      moveit::core::RobotState state(model);
      state.setToDefaultValues();
      std::map<std::string, double> positions;
      for (const auto& name : variable_names)
        positions.emplace(name, number(row.at(name), name));
      state.setVariablePositions(positions);
      state.update();
      add_attachments(state, scenario, attachments);

      const auto world = make_world(scenario, worlds);
      collision_detection::CollisionEnvFCL environment(model, world);
      collision_detection::DistanceRequest request;
      request.type = collision_detection::DistanceRequestTypes::SINGLE;
      request.max_contacts_per_body = 1;
      request.enable_nearest_points = true;
      request.enable_signed_distance = true;
      request.distance_threshold = std::numeric_limits<double>::max();
      request.acm = &acm;
      request.enableGroup(model);
      collision_detection::DistanceResult self_result;
      collision_detection::DistanceResult world_result;
      environment.distanceSelf(request, self_result, state);
      environment.distanceRobot(request, world_result, state);
      std::set<std::tuple<std::string, std::string, std::string>> unique;
      append_distances(scenario, "self", self_result, output, unique);
      append_distances(scenario, "world", world_result, output, unique);
    }
    std::sort(output.begin(), output.end(), [](const OutputRow& first, const OutputRow& second) {
      return std::tie(first.scenario, first.scope, first.first, first.second) <
             std::tie(second.scenario, second.scope, second.first, second.second);
    });
    write_output(argv[6], output);
    std::cout << "{\"status\":\"PASS\",\"request_type\":\"SINGLE\",\"scenarios\":" << scenarios.size()
              << ",\"distance_rows\":" << output.size()
              << ",\"signed_distance\":true,\"nearest_points\":true}\n";
    return 0;
  }
  catch (const std::exception& error)
  {
    std::cerr << "openarm_fcl_pair_probe: " << error.what() << '\n';
    return 1;
  }
}
