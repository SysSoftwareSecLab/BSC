#define main bisafecode_packaged_probe_original_main
#include "openarm_fcl_pair_probe.cpp"
#undef main

#include <chrono>
#include <ctime>

namespace
{
std::string json_escape(const std::string& value)
{
  std::ostringstream output;
  for (const unsigned char character : value)
  {
    switch (character)
    {
      case '\\':
        output << "\\\\";
        break;
      case '"':
        output << "\\\"";
        break;
      case '\n':
        output << "\\n";
        break;
      case '\r':
        output << "\\r";
        break;
      case '\t':
        output << "\\t";
        break;
      default:
        if (character < 0x20)
          output << "\\u" << std::hex << std::setw(4) << std::setfill('0') << static_cast<int>(character)
                 << std::dec << std::setfill(' ');
        else
          output << character;
    }
  }
  return output.str();
}

std::pair<std::size_t, std::size_t> process_request(
    const moveit::core::RobotModelConstPtr& model, const collision_detection::AllowedCollisionMatrix& acm,
    const std::filesystem::path& states_path, const std::filesystem::path& world_path,
    const std::filesystem::path& attachments_path, const std::filesystem::path& output_path,
    std::unique_ptr<collision_detection::CollisionEnvFCL>& persistent_environment,
    std::string& persistent_world_identity)
{
  const auto& variable_names = model->getVariableNames();
  const auto states = read_state_tsv(states_path, variable_names);
  const auto worlds = read_tsv(world_path, { "scenario", "object_id", "center_x", "center_y", "center_z",
                                              "size_x", "size_y", "size_z" });
  const auto attachments = read_tsv(
      attachments_path, { "scenario", "body_id", "parent_link", "size_x", "size_y", "size_z", "pose_x",
                          "pose_y", "pose_z", "pose_qx", "pose_qy", "pose_qz", "pose_qw", "touch_links_csv" });
  if (states.empty())
    throw std::runtime_error("states table contains no scenarios");
  const auto world_identity = read_file(world_path);
  if (!persistent_environment)
  {
    for (const auto& row : worlds)
      if (row.at("scenario") != "*")
        throw std::runtime_error("persistent server requires scenario-invariant '*' world rows");
    persistent_world_identity = world_identity;
    persistent_environment =
        std::make_unique<collision_detection::CollisionEnvFCL>(model, make_world(states.front().at("scenario"), worlds));
  }
  else if (world_identity != persistent_world_identity)
  {
    throw std::runtime_error("world TSV changed within one persistent program run");
  }

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
    persistent_environment->distanceSelf(request, self_result, state);
    persistent_environment->distanceRobot(request, world_result, state);
    std::set<std::tuple<std::string, std::string, std::string>> unique;
    append_distances(scenario, "self", self_result, output, unique);
    append_distances(scenario, "world", world_result, output, unique);
  }
  std::sort(output.begin(), output.end(), [](const OutputRow& first, const OutputRow& second) {
    return std::tie(first.scenario, first.scope, first.first, first.second) <
           std::tie(second.scenario, second.scope, second.first, second.second);
  });
  write_output(output_path, output);
  return { scenarios.size(), output.size() };
}
}  // namespace

int main(int argc, char** argv)
{
  if (argc != 3)
  {
    std::cerr << "usage: openarm_fcl_pair_probe_server URDF SRDF\n";
    return 2;
  }
  try
  {
    const auto load_wall_start = std::chrono::steady_clock::now();
    const auto load_cpu_start = std::clock();
    auto urdf = urdf::parseURDF(read_file(argv[1]));
    if (!urdf)
      throw std::runtime_error("URDF parse failed");
    auto srdf = std::make_shared<srdf::Model>();
    if (!srdf->initString(*urdf, read_file(argv[2])))
      throw std::runtime_error("SRDF parse failed");
    auto model = std::make_shared<moveit::core::RobotModel>(urdf, srdf);
    collision_detection::AllowedCollisionMatrix acm(*srdf);
    const auto load_wall_ns =
        std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now() - load_wall_start)
            .count();
    const auto load_cpu_ns = static_cast<long long>(
        static_cast<long double>(std::clock() - load_cpu_start) * 1000000000.0L / CLOCKS_PER_SEC);
    std::cout << "{\"status\":\"READY\",\"model_load_wall_ns\":" << load_wall_ns
              << ",\"model_load_cpu_ns\":" << load_cpu_ns << "}\n"
              << std::flush;

    std::string line;
    std::size_t request_index = 0;
    std::unique_ptr<collision_detection::CollisionEnvFCL> persistent_environment;
    std::string persistent_world_identity;
    while (std::getline(std::cin, line))
    {
      if (line == "QUIT")
      {
        std::cout << "{\"status\":\"BYE\",\"requests\":" << request_index << "}\n" << std::flush;
        return 0;
      }
      const auto fields = split(line, '\t');
      if (fields.size() != 4)
        throw std::runtime_error("server request must contain four tab-separated paths");
      try
      {
        const auto [scenarios, rows] = process_request(model, acm, fields[0], fields[1], fields[2], fields[3],
                                                       persistent_environment, persistent_world_identity);
        std::cout << "{\"status\":\"PASS\",\"request_index\":" << request_index
                  << ",\"scenarios\":" << scenarios << ",\"distance_rows\":" << rows << "}\n"
                  << std::flush;
      }
      catch (const std::exception& error)
      {
        std::cout << "{\"status\":\"ERROR\",\"request_index\":" << request_index
                  << ",\"message\":\"" << json_escape(error.what()) << "\"}\n"
                  << std::flush;
      }
      ++request_index;
    }
    return 0;
  }
  catch (const std::exception& error)
  {
    std::cerr << "openarm_fcl_pair_probe_server: " << error.what() << '\n';
    return 1;
  }
}
